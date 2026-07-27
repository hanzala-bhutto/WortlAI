import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

import { Home } from "./routes/Home";
import { Talk } from "./routes/Talk";

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

const talkRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/talk",
  component: Talk,
});

// Routes are defined in code while there are few of them. The review and
// dashboard routes land with their own issues (#17, #27).
const routeTree = rootRoute.addChildren([indexRoute, talkRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
