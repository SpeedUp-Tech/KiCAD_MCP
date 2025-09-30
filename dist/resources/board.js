/**
 * Board resources (disabled in multi-session mode).
 */
import { logger } from '../logger.js';
export function registerBoardResources(server, _callKicadScript) {
    logger.warn('Board resources are not registered in multi-session mode.');
}
//# sourceMappingURL=board.js.map