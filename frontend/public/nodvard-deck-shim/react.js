// Import-Map-Shim (docs/02-EXTENSION-API.md §5): loest die "react"-Spezifikation
// eines per import() nachgeladenen Extension-Bundles auf DIESE Datei auf (siehe
// index.html <script type="importmap">), die wiederum die EINE, vom Kern-Bundle
// bereits geladene React-Instanz aus window.__nodvardDeck weiterreicht -- keine zweite
// Kopie von React im Speicher, kein "invalid hook call" durch zwei React-Instanzen.
const React = window.__nodvardDeck.React;
export default React;
export const {
  Children, Component, Fragment, PureComponent, StrictMode, Suspense,
  cloneElement, createContext, createElement, createRef, forwardRef,
  isValidElement, lazy, memo, useCallback, useContext, useDebugValue,
  useDeferredValue, useEffect, useId, useImperativeHandle, useInsertionEffect,
  useLayoutEffect, useMemo, useReducer, useRef, useState, useSyncExternalStore,
  useTransition, version,
} = React;
