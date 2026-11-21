import { redirect } from 'next/navigation';

// Comments live on each job and order now.
export default function Page() {
  redirect('/inbox');
}
