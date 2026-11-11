import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  {
    // Panel pobiera dane z API w efektach (bez biblioteki do danych), więc ustawianie stanu
    // w efekcie jest tu zamierzone.
    rules: { "react-hooks/set-state-in-effect": "off" },
  },
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts", "e2e/**"] },
];

export default config;
