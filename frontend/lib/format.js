/** Most holes have hole_name === hole_id (e.g. "KD1"/"KD1") - only worth
 * showing the name separately when it actually says something extra. */
export function distinctName(hole) {
  if (!hole?.hole_name || hole.hole_name === hole.hole_id) return null;
  return hole.hole_name;
}
