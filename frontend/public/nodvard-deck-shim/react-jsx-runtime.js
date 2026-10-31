// Import-Map-Shim fuer "react/jsx-runtime" -- gebraucht von jedem Extension-Bundle,
// das mit der automatischen JSX-Transform gebaut wurde (esbuild --jsx=automatic,
// die von Vite/tsc genauso gesetzte Voreinstellung). Siehe react.js fuer die volle
// Begruendung.
const { jsx, jsxs, Fragment } = window.__nodvardDeck.ReactJsxRuntime;
export { jsx, jsxs, Fragment };
