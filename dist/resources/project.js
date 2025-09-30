/**
 * Project resources (disabled in multi-session mode).
 */
import { logger } from '../logger.js';
export function registerProjectResources(server, _callKicadScript) {
    logger.warn('Project resources are not registered in multi-session mode.');
}
//# sourceMappingURL=project.js.map