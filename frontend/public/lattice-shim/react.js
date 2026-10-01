// Import-Map-Shim (docs/02-EXTENSION-API.md §5): loest die "react"-Spezifikation
// eines per import() nachgeladenen Extension-Bundles auf DIESE Datei auf (siehe
// index.html <script type="importmap">), die wiederum die EINE, vom Kern-Bundle
// bereits geladene React-Instanz aus window.__nodvardDeck weiterreicht -- keine zweite
// Kopie von React im Speicher, kein "invalid hook call" durch zwei React-Instanzen.
//
// `window.__nodvardDeck ?? window.__lattice`: ein Tab, der vor einem Update geladen wurde,
// hat noch den alten Kern (nur `__lattice`) und holt trotzdem diese Datei. Beide Namen sind
// dasselbe Objekt. Der Pfad /lattice-shim/ bleibt aus demselben Grund: die Import-Map eines
// alten Tabs verweist darauf (siehe index.html).
const React = (window.__nodvardDeck ?? window.__lattice).React;
export default React;
export const {
  Children, Component, Fragment, PureComponent, StrictMode, Suspense,
  cloneElement, createContext, createElement, createRef, forwardRef,
  isValidElement, lazy, memo, useCallback, useContext, useDebugValue,
  useDeferredValue, useEffect, useId, useImperativeHandle, useInsertionEffect,
  useLayoutEffect, useMemo, useReducer, useRef, useState, useSyncExternalStore,
  useTransition, version,
} = React;
