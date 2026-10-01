// Import-Map-Shim fuer "react/jsx-runtime" -- gebraucht von jedem Extension-Bundle,
// das mit der automatischen JSX-Transform gebaut wurde (esbuild --jsx=automatic,
// die von Vite/tsc genauso gesetzte Voreinstellung). Siehe react.js fuer die volle
// Begruendung.
// Beide Namen des globalen Objekts (siehe react.js): neu `__nodvardDeck`, alt `__lattice`.
const { jsx, jsxs, Fragment } = (window.__nodvardDeck ?? window.__lattice).ReactJsxRuntime;
export { jsx, jsxs, Fragment };
