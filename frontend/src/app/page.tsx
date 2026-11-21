import { redirect } from 'next/navigation';

// Home is Today: what is waiting on you. The boards are one click away at /boards.
export default function Home() {
  redirect('/inbox');
}
