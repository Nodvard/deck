import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useEdition } from "../state/edition";
import { NotFoundPage } from "../routes/ErrorPages";

export const DOCK_HOME = "/ext/docker/matrix";

export function editionPathAvailable(path: string, chooseModules: boolean): boolean {
  if (chooseModules) return true;
  return !/^\/(apps|files|hosts|console)(\/|$)/.test(path)
    && (!path.startsWith("/ext/") || /^\/ext\/(docker|service-matrix)(\/|$)/.test(path))
    && (!path.startsWith("/settings/extensions/") || /^\/settings\/extensions\/(docker|service-matrix)\/?$/.test(path));
}

export function EditionRoutes() {
  const edition = useEdition();
  const { pathname } = useLocation();
  if (!editionPathAvailable(pathname, edition.choose_modules)) return <NotFoundPage />;
  if (!edition.choose_modules && pathname === "/") return <Navigate to={DOCK_HOME} replace />;
  if (!edition.choose_modules && pathname === "/settings/extensions") return <Navigate to="/settings/extensions/docker" replace />;
  return <Outlet />;
}
