"""In-memory RapidFuzz catalog lookup, alias normalization, and entity resolution."""

import csv
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from rapidfuzz import fuzz, process

from app.config import settings
from app.core.exceptions import EntityNotFoundError
from app.core.logging import logger


# Prefix width for the scan prefilter. Three characters is long enough to
# split a catalogue into many buckets and short enough that every real SKU and
# alias is at least that long.
PREFIX_LEN = 3


@lru_cache(maxsize=4096)
def _term_pattern(term: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![A-Z0-9])" + re.escape(term) + r"(?![A-Z0-9])")


def contains_term(haystack_upper: str, term_upper: str) -> bool:
    """True if term occurs in the text with no letter or digit either side of it."""
    return bool(term_upper) and _term_pattern(term_upper).search(haystack_upper) is not None


class EntityMatcher:
    """Zero-latency in-memory resolver for SKUs, customer accounts, and vendor routing."""

    def __init__(
        self,
        items_csv_path: Optional[Path] = None,
        customers_csv_path: Optional[Path] = None,
        vendors_csv_path: Optional[Path] = None,
        alias_json_path: Optional[Path] = None,
    ):
        self.items_path = items_csv_path or settings.MASTER_ITEMS_PATH
        self.customers_path = customers_csv_path or settings.MASTER_CUSTOMERS_PATH
        self.vendors_path = vendors_csv_path or settings.MASTER_VENDORS_PATH
        self.alias_path = alias_json_path or settings.ALIAS_DICT_PATH

        self.items_cache: Dict[str, Dict[str, Any]] = {}
        self.customers_cache: Dict[str, Dict[str, Any]] = {}
        self.vendors_cache: Dict[str, Dict[str, Any]] = {}
        self.alias_dict: Dict[str, str] = {}
        # Derived longest-first scan tables, rebuilt by load_catalogs().
        self.items_by_length: List[Tuple[str, Dict[str, Any]]] = []
        self.aliases_by_length: List[Tuple[str, str]] = []
        self._last_loaded_mtimes: Dict[str, float] = {}

        self.load_catalogs()

    def check_and_reload_if_stale(self) -> bool:
        """Checks if any master CSV/JSON file on disk has been updated since last load."""
        stale = False
        for p in [self.items_path, self.customers_path, self.vendors_path, self.alias_path]:
            if p and p.exists():
                try:
                    mtime = p.stat().st_mtime
                    if mtime > self._last_loaded_mtimes.get(str(p), 0):
                        stale = True
                        break
                except Exception:
                    pass
        if stale:
            self.load_catalogs()
            return True
        return False

    def load_catalogs(self) -> None:
        """Loads all CSV and JSON catalogs into memory."""
        self._last_loaded_mtimes = {}
        for p in [self.items_path, self.customers_path, self.vendors_path, self.alias_path]:
            if p and p.exists():
                try:
                    self._last_loaded_mtimes[str(p)] = p.stat().st_mtime
                except Exception:
                    pass

        # Load items
        if self.items_path.exists():
            with open(self.items_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.items_cache = {row["item_id"]: row for row in reader}
            logger.info(f"Loaded {len(self.items_cache)} items into memory")

        # Load customers
        if self.customers_path.exists():
            with open(self.customers_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.customers_cache = {row["customer_id"]: row for row in reader}
            logger.info(f"Loaded {len(self.customers_cache)} customers into memory")

        # Load vendors
        if self.vendors_path.exists():
            with open(self.vendors_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.vendors_cache = {row["vendor_id"]: row for row in reader}
            logger.info(f"Loaded {len(self.vendors_cache)} vendors into memory")

        # Load alias dictionary
        if self.alias_path.exists():
            with open(self.alias_path, mode="r", encoding="utf-8") as f:
                self.alias_dict = json.load(f)
            logger.info(f"Loaded {len(self.alias_dict)} alias mappings")

        self._build_scan_index()

    def _build_scan_index(self) -> None:
        """Precomputes the longest-first lookup tables used by text scanners.

        Document and email parsers match a line against every SKU and alias,
        longest first, so that PLATE-300XL-38 wins over a shorter SKU contained
        inside it. Each scanner used to sort both collections itself -- the
        document scanner did it once per line of every document, which made
        scan cost grow with catalogue size far faster than it needed to. The
        ordering only changes when a catalogue is reloaded, so it is derived
        once here and shared by every consumer.

        Keys are pre-uppercased so the scanners compare without re-casing.
        """
        self.items_by_length: List[Tuple[str, Dict[str, Any]]] = sorted(
            ((item_id.upper(), rec) for item_id, rec in self.items_cache.items()),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        self.aliases_by_length: List[Tuple[str, str]] = sorted(
            ((alias.upper(), sku) for alias, sku in self.alias_dict.items()),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        self._items_by_prefix = self._index_by_prefix(self.items_by_length)
        self._aliases_by_prefix = self._index_by_prefix(self.aliases_by_length)
        self._build_match_corpora()

    def _build_match_corpora(self) -> None:
        """Precomputes the normalized corpora the fuzzy matchers search.

        match_item, match_customer and match_vendor each rebuilt their corpus
        on every call: normalizing every description, every alias key and every
        customer profile per lookup. Matching runs once per order line, so that
        work was repeated for every line of every order. None of it changes
        between catalogue loads.

        Insertion order is preserved so that first-match-wins rules behave
        exactly as they did when iterating the caches directly.
        """
        # match_item step 1.5: the "core" part of a SKU, e.g. CHAN-C12X30 -> c12x30
        self._item_core_codes: List[Tuple[str, str, Dict[str, Any]]] = []
        for item_id, item_data in self.items_cache.items():
            parts = item_id.split("-", 1)
            if len(parts) > 1 and len(parts[1]) >= 4:
                self._item_core_codes.append(
                    (parts[1].replace("-", "").lower(), parts[1].lower(), item_data)
                )

        # match_item steps 2 and 3: alias keys, normalized once.
        self._alias_norms: List[str] = []
        self._alias_norm_to_sku: Dict[str, str] = {}
        for alias_key, target_sku in self.alias_dict.items():
            norm = self.normalize_text(alias_key)
            self._alias_norms.append(norm)
            self._alias_norm_to_sku.setdefault(norm, target_sku)

        # match_item step 4: item id + description + category.
        self._item_corpus: Dict[str, str] = {
            item_id: self.normalize_text(
                f"{item_id} {data.get('description', '')} {data.get('category', '')}"
            )
            for item_id, data in self.items_cache.items()
        }

        # match_customer: name-only pass, then the full profile pass.
        self._customer_names: Dict[str, str] = {
            cid: self.normalize_text(f"{cid} {c.get('customer_name', '')}")
            for cid, c in self.customers_cache.items()
        }
        self._customer_corpus: Dict[str, str] = {
            cid: self.normalize_text(
                f"{cid} {c.get('customer_name', '')} {c.get('shipping_address', '')} "
                f"{c.get('contact_name', '')}"
            )
            for cid, c in self.customers_cache.items()
        }

        # match_vendor: same two passes.
        self._vendor_names: Dict[str, str] = {
            vid: self.normalize_text(f"{vid} {v.get('vendor_name', '')}")
            for vid, v in self.vendors_cache.items()
        }
        self._vendor_corpus: Dict[str, str] = {
            vid: self.normalize_text(
                f"{vid} {v.get('vendor_name', '')} {v.get('contact_name', '')} "
                f"{v.get('category', '')} {v.get('vendor_address', '')}"
            )
            for vid, v in self.vendors_cache.items()
        }

    @staticmethod
    def _index_by_prefix(pairs: List[Tuple[str, Any]]) -> Dict[str, List[Tuple[str, Any]]]:
        """Buckets scan entries by their first PREFIX_LEN characters.

        Used as an exact prefilter: if a key appears anywhere in a line of
        text, that key's own prefix necessarily appears in the same line. So
        collecting the buckets for the prefixes actually present narrows the
        candidates without ever losing a match. Keys shorter than the prefix
        length go in the "" bucket and are always considered.
        """
        index: Dict[str, List[Tuple[str, Any]]] = {}
        for key, value in pairs:
            bucket = key[:PREFIX_LEN] if len(key) >= PREFIX_LEN else ""
            index.setdefault(bucket, []).append((key, value))
        return index

    def scan_candidates(self, haystack_upper: str, aliases: bool = False) -> List[Tuple[str, Any]]:
        """Returns the scan entries that could occur in `haystack_upper`.

        Ordered longest key first, so a caller taking the first containment hit
        gets the most specific match (PLATE-300XL-38 before 300XL).
        """
        index = self._aliases_by_prefix if aliases else self._items_by_prefix
        if not index:
            return []

        present = {
            haystack_upper[i:i + PREFIX_LEN]
            for i in range(max(0, len(haystack_upper) - PREFIX_LEN + 1))
        }
        candidates: List[Tuple[str, Any]] = list(index.get("", ()))
        for prefix in present:
            bucket = index.get(prefix)
            if bucket:
                candidates.extend(bucket)

        if aliases:
            # Aliases are shop words, so they match whole terms only: a short
            # alias like "AR" must not hit BAR or WEAR inside a longer word.
            candidates = [pair for pair in candidates if contains_term(haystack_upper, pair[0])]
        candidates.sort(key=lambda pair: len(pair[0]), reverse=True)
        return candidates

    @staticmethod
    def normalize_text(text: str) -> str:
        """Strips special characters and standardizes whitespace and casing."""
        if not text:
            return ""
        cleaned = re.sub(r"[^a-zA-Z0-9\s\.\-\/]", " ", text)
        return " ".join(cleaned.lower().split())

    def match_item(
        self,
        query: str,
        min_confidence: float = 70.0,
        _retry: bool = True,
    ) -> Tuple[Dict[str, Any], float]:
        """
        Resolves query to standard Sage 50 Item Master record.
        Multi-tier strategy:
        1. Exact item_id match.
        2. Alias dictionary lookup.
        3. Normalized fuzzy token_sort_ratio against descriptions & item IDs.
        """
        raw_query = query.strip()
        norm_query = self.normalize_text(raw_query)

        # 1. Exact Item ID
        if raw_query in self.items_cache:
            return self.items_cache[raw_query], 100.0
        upper_query = raw_query.upper()
        if upper_query in self.items_cache:
            return self.items_cache[upper_query], 100.0

        # 1.5. Core SKU Identifier Match (e.g. 'c12x30' for 'CHAN-C12X30' or 'w8x31' for 'BEAM-W8X31')
        clean_tokens = norm_query.replace("-", "").replace(" ", "")
        for core_code, suffix_lower, item_data in self._item_core_codes:
            if core_code in clean_tokens or norm_query.startswith(suffix_lower):
                return item_data, 96.0

        # 2. Alias Dictionary Lookup
        target_sku = self._alias_norm_to_sku.get(norm_query)
        if target_sku and target_sku in self.items_cache:
            return self.items_cache[target_sku], 98.0

        # 3. Fuzzy Match against Alias Keys
        alias_matches = process.extract(
            norm_query,
            self._alias_norms,
            scorer=fuzz.token_sort_ratio,
            limit=1,
        )
        if alias_matches and alias_matches[0][1] >= 85.0:
            matched_norm_alias = alias_matches[0][0]
            # Ensure numbers in query match numbers in alias to prevent dimensional collision (e.g. C12x30 vs C10x20)
            query_nums = set(re.findall(r"\d+", norm_query))
            alias_nums = set(re.findall(r"\d+", matched_norm_alias))
            if not query_nums or query_nums.issubset(alias_nums) or alias_nums.issubset(query_nums):
                matched_sku = self._alias_norm_to_sku.get(matched_norm_alias)
                if matched_sku and matched_sku in self.items_cache:
                    return self.items_cache[matched_sku], float(alias_matches[0][1])

        # 4. Fuzzy Match against Master Items (descriptions + item_ids)
        matches = process.extract(
            norm_query,
            self._item_corpus,
            scorer=fuzz.token_set_ratio,
            limit=1,
        )

        if matches and matches[0][1] >= min_confidence:
            matched_item_id = matches[0][2]
            score = float(matches[0][1])
            return self.items_cache[matched_item_id], score

        if _retry and self.check_and_reload_if_stale():
            return self.match_item(query, min_confidence=min_confidence, _retry=False)

        raise EntityNotFoundError(
            f"Unable to resolve item '{query}' against Sage 50 catalog (threshold: {min_confidence}%).",
            details={"query": query, "best_match": matches[0] if matches else None},
        )

    def match_customer(
        self,
        query: str,
        min_confidence: float = 65.0,
        _retry: bool = True,
    ) -> Tuple[Dict[str, Any], float]:
        """Resolves customer name or shorthand to Sage 50 Customer Master record."""
        raw_query = query.strip()
        norm_query = self.normalize_text(raw_query)

        # 1. Exact Customer ID
        if raw_query in self.customers_cache:
            return self.customers_cache[raw_query], 100.0

        # 2. Known Customer Aliases
        customer_shorthand: Dict[str, str] = {
            "oc-edmonton": "10221",
            "owens corning edmonton": "10221",
            "oc edmonton": "10221",
            "owens corning insulating systems canada": "10221",
            "oc-starr": "10195",
            "owens corning starr": "10195",
            "owens corning starr composites": "10195",
            "starr composites": "10195",
            "oc starr": "10195",
            "knauf": "10148",
            "knauf insulation": "10148",
            "knauf lanett": "10148",
            "knauf-lanett": "10148",
            "iko": "681111",
            "iko-sw": "681111",
            "iko southwest": "681111",
            "iko hillsboro": "681111",
        }

        if norm_query in customer_shorthand:
            cust_id = customer_shorthand[norm_query]
            return self.customers_cache[cust_id], 99.0
        # In a company name a hyphen joins words ("Owens-Corning"); read it as a space from here
        # on, or the joined word matches neither half. (Part numbers keep theirs: items only.)
        norm_query = " ".join(norm_query.replace("-", " ").split())

        # Disambiguate multi-plant customers by plant keyword. This only
        # applies when the query also names the parent account: a bare
        # substring test resolved "Starrett Precision Tool" to Owens Corning
        # Starr, and any Edmonton-based customer to Owens Corning Edmonton.
        query_tokens = set(norm_query.split())
        is_owens = bool(query_tokens & {"oc", "owens", "corning"})
        if is_owens and "starr" in query_tokens and "10195" in self.customers_cache:
            return self.customers_cache["10195"], 98.0
        if is_owens and "edmonton" in query_tokens and "10221" in self.customers_cache:
            return self.customers_cache["10221"], 98.0

        # 2.5 Primary search on customer name & ID
        name_matches = process.extract(
            norm_query,
            self._customer_names,
            scorer=fuzz.token_set_ratio,
            limit=2,
        )
        if name_matches and name_matches[0][1] >= min_confidence:
            matched_id = name_matches[0][2]
            return self.customers_cache[matched_id], float(name_matches[0][1])

        # 3. Fuzzy search on full profiles including addresses
        matches = process.extract(
            norm_query,
            self._customer_corpus,
            scorer=fuzz.token_set_ratio,
            limit=2,
        )

        if matches and matches[0][1] >= min_confidence:
            matched_id = matches[0][2]
            return self.customers_cache[matched_id], float(matches[0][1])

        if _retry and self.check_and_reload_if_stale():
            return self.match_customer(query, min_confidence=min_confidence, _retry=False)

        raise EntityNotFoundError(
            f"Unable to resolve customer '{query}' (threshold: {min_confidence}%).",
            details={"query": query},
        )

    def match_vendor(
        self,
        query: str,
        min_confidence: float = 65.0,
        _retry: bool = True,
    ) -> Tuple[Dict[str, Any], float]:
        """Resolves vendor name or shorthand to Sage 50 Vendor Master record."""
        raw_query = query.strip()
        norm_query = self.normalize_text(raw_query)

        # 1. Exact Vendor ID
        upper_query = raw_query.upper()
        if upper_query in self.vendors_cache:
            return self.vendors_cache[upper_query], 100.0

        # 2. Known Vendor Aliases
        vendor_aliases: Dict[str, str] = {
            "helen": "ZIBO",
            "zibo": "ZIBO",
            "zibo supereal": "ZIBO",
            "duke": "NIBROCK",
            "duke parker": "NIBROCK",
            "nibrock": "NIBROCK",
            "kws": "KWS",
            "kws manufacturing": "KWS",
            "internal": "STOCK",
            "stock": "STOCK",
            "shop": "STOCK",
            "test mill": "TEST_MILL",
            "test mills": "TEST_MILL",
        }

        if norm_query in vendor_aliases:
            vend_id = vendor_aliases[norm_query]
            return self.vendors_cache[vend_id], 99.0

        # 2.5 Primary search on vendor name & ID
        name_matches = process.extract(
            norm_query,
            self._vendor_names,
            scorer=fuzz.token_set_ratio,
            limit=2,
        )
        if name_matches and name_matches[0][1] >= min_confidence:
            matched_id = name_matches[0][2]
            return self.vendors_cache[matched_id], float(name_matches[0][1])

        # 3. Fuzzy search on full profiles including addresses
        matches = process.extract(
            norm_query,
            self._vendor_corpus,
            scorer=fuzz.token_set_ratio,
            limit=1,
        )

        if matches and matches[0][1] >= min_confidence:
            matched_id = matches[0][2]
            return self.vendors_cache[matched_id], float(matches[0][1])

        if _retry and self.check_and_reload_if_stale():
            return self.match_vendor(query, min_confidence=min_confidence, _retry=False)

        raise EntityNotFoundError(
            f"Unable to resolve vendor '{query}' (threshold: {min_confidence}%).",
            details={"query": query},
        )
