// js-yaml ships without types and only `load` is used (src/components/YamlView.tsx).
declare module "js-yaml" {
  export function load(text: string): unknown;
}
