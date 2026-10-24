# m-ts-weak

A tiny order-cart rules library in TypeScript. `src/cart.ts` decides whether
an order qualifies for the quantity discount; `test/cart.test.ts` covers it
with node's built-in test runner (node runs `.ts` natively, so there is no
build step and nothing to install).

Run the tests:

    npm test

which is exactly `node --test test/`.
