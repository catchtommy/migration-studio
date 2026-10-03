/** A key derived from a title: lowercase letters, digits and the separator, starting with a letter. */
export function slug(text: string, sep: "_" | "-" = "_"): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, sep)
    .replace(/^[^a-z]+/, "")
    .replace(/[-_]+$/, "");
}
