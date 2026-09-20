import { setupServer } from "msw/node";

import { publicApiHandlers } from "./handlers";

export const mockApiServer = setupServer(...publicApiHandlers);
