import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

import { Home } from "./routes/Home";

const rootRoute = createRootRoute({
  component: () => (
    <div className="flex min-h-full flex-col">
      <Outlet />
    </div>
  ),
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: Home,
});

// Routes are defined in code while there are few of them. The talk, review and
// dashboard routes land with their own issues (#8, #17, #27).
const routeTree = rootRoute.addChildren([indexRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
