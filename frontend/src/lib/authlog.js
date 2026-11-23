// A small in-memory record of every sign-in renewal: when, why it ran, what the server said and what the app did next.
// Shown to admins at the foot of the System page, and printed to the browser console, so a lost session can be explained.
const LIMIT = 60;
const entries = [];

export function logRefresh(entry) {
  const row = { at: new Date().toISOString(), ...entry };
  entries.push(row);
  if (entries.length > LIMIT) entries.shift();
  try {
    console.info('[allied renew]', row);
  } catch {
    // No console: nothing to do.
  }
}

export const refreshLog = () => entries.slice().reverse();
