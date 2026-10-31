// Import-Map-Shim fuer "react-dom" -- siehe react.js fuer die volle Begruendung.
// Seltener direkt gebraucht (Seiten werden vom Kern-Router gerendert, nicht von der
// Extension selbst gemountet), aber Teil des in docs/02 §5 genannten Vertrags
// ("react", "react-dom" und "react/jsx-runtime" werden aufgeloest).
const ReactDOM = window.__nodvardDeck.ReactDOM;
export default ReactDOM;
export const { createPortal, findDOMNode, flushSync, unstable_batchedUpdates } = ReactDOM;
