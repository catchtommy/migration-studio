/** Splits an Oracle connection string pasted into the Host field (JDBC thin URL, Easy Connect or a TNS descriptor)
 * into host, port and service name. Returns null for a plain host name. */
export function parseOracleConnect(text: string): { host: string; port: number | null; service: string } | null {
  const value = text.trim();
  if (/\(\s*DESCRIPTION/i.test(value)) {
    const pick = (key: string) => value.match(new RegExp(`\\(\\s*${key}\\s*=\\s*([^)\\s]+)`, "i"))?.[1] ?? "";
    const host = pick("HOST");
    return host ? { host, port: Number(pick("PORT")) || null, service: pick("SERVICE_NAME") || pick("SID") } : null;
  }
  if (!/[@/:]/.test(value)) return null;
  let rest = value.replace(/^jdbc:oracle:(thin|oci):/i, "");
  rest = rest.slice(rest.lastIndexOf("@") + 1).replace(/^\/\//, ""); // drop user/password@ and the // of Easy Connect
  const m = rest.match(/^([^:/]+)(?::(\d+))?(?:[/:]([^/?\s]+))?/);
  if (!m) return null;
  return { host: m[1], port: m[2] ? Number(m[2]) : null, service: m[3] ?? "" };
}
