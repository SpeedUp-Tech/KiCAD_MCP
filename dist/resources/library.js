/**
 * Library resources (disabled in multi-session mode).
 */
import { logger } from '../logger.js';
export function registerLibraryResources(server, _callKicadScript) {
    logger.warn('Library resources are not registered in multi-session mode.');
}
//# sourceMappingURL=library.js.map