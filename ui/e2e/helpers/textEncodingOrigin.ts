// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
export function textEncodingOrigin(suppliedURL?: string): string {
  const url = new URL(suppliedURL || "http://127.0.0.1:4187");
  if (
    url.protocol !== "http:" ||
    !["localhost", "127.0.0.1"].includes(url.hostname) ||
    url.username || url.password ||
    url.pathname !== "/" || url.search || url.hash
  ) {
    throw new Error("Text encoding fixtures require a plain loopback HTTP origin");
  }
  return url.origin;
}
