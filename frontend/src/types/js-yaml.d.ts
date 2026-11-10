// js-yaml ships without types: only `load` (src/components/YamlView.tsx) and `dump` (FunctionalAllocationView) are used.
declare module "js-yaml" {
  export function load(text: string): unknown;
  export function dump(value: unknown, options?: { lineWidth?: number; noRefs?: boolean; sortKeys?: boolean }): string;
}
