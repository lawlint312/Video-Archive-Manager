// Lets Node import this project's extensionless specifiers.
//
// The source is written for Vite, which resolves `../store` to `../store.js`.
// Node's ESM loader does not, and the failure surfaces deep in the dependency
// graph (a component imports `../store`, which imports `./videosearch`, …), so
// rewriting only the component's own imports is not enough.
export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context)
  } catch (e) {
    if (specifier.startsWith('.') && !/\.[a-z]+$/.test(specifier)) {
      return next(specifier + '.js', context)
    }
    throw e
  }
}
