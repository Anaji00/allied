/**
 * The engineers, always offered in every people list. Their emails come from the app's contacts and users;
 * until someone is added there they still show (greyed out, "no email yet") so nobody seems to be missing.
 */
export const ENGINEERS = [
  'Paul Wang',
  'Taras Zhurakovskyi',
  'Allen Steeves',
  'Erin Smith',
  'Quan Tran',
  'Shawn Bettencourt',
  'Shane Flaherty',
];

type Person = { email: string; name: string; role: string; label: string };

const norm = (t: string) =>
  t
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, '')
    .replace(/\s+/g, ' ')
    .trim();

export function withEngineers<T extends Person>(people: T[]): Person[] {
  const out: Person[] = people.map((p) => ({ ...p }));
  for (const name of ENGINEERS) {
    const found = out.find((p) => p.email && norm(p.name) === norm(name));
    if (found) found.role = 'engineer';
    else out.push({ email: '', name, role: 'engineer', label: `${name} (no email yet)` });
  }
  return out.sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email));
}
