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

/**
 * Leadership who use the platform. Offered in every people list, like the engineers. No email is guessed: until one is added
 * in the app's contacts or users they show greyed out, "no email yet".
 */
export const LEADERSHIP = [
  { name: 'Doug Hamlin', title: 'Founder and CEO' },
  { name: 'Athish Singh', title: 'Solutions engineer' },
];

/** The sales rep on every customer record. Offered in people lists; her title is not shown anywhere, so none is claimed. */
export const SALES = ['Karissa M. Rakestraw'];

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
  for (const { name, title } of LEADERSHIP) {
    const first = norm(name).split(' ')[0];
    const found = out.find((p) => p.email && (norm(p.name) === norm(name) || (first.length > 3 && norm(p.name).startsWith(`${first} `))));
    if (found) {
      found.role = 'leadership';
      found.label = `${found.name || name}, ${title.toLowerCase()}`;
    } else out.push({ email: '', name, role: 'leadership', label: `${name}, ${title.toLowerCase()} (no email yet)` });
  }
  for (const name of SALES) {
    const found = out.find((p) => p.email && norm(p.name) === norm(name));
    if (found) found.role = 'sales';
    else out.push({ email: '', name, role: 'sales', label: `${name}, office manager (no email yet)` });
  }
  return out.sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email));
}
