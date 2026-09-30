import { redirect } from "next/navigation";

// Compare is part of the 3D view now (two cores side by side) - this keeps
// old links and bookmarks (/compare?a=...&b=...) working.
export default function ComparePage({ searchParams }) {
  redirect(`/viewer3d?${new URLSearchParams(searchParams)}`);
}
