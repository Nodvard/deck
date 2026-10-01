// Import-Map-Shim fuer "react-dom" -- siehe react.js fuer die volle Begruendung.
// Seltener direkt gebraucht (Seiten werden vom Kern-Router gerendert, nicht von der
// Extension selbst gemountet), aber Teil des in docs/02 §5 genannten Vertrags
// ("react", "react-dom" und "@lattice/extension-sdk" werden aufgeloest).
// Beide Namen des globalen Objekts (siehe react.js): neu `__nodvardDeck`, alt `__lattice`.
const ReactDOM = (window.__nodvardDeck ?? window.__lattice).ReactDOM;
export default ReactDOM;
export const { createPortal, findDOMNode, flushSync, unstable_batchedUpdates } = ReactDOM;
