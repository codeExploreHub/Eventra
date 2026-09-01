# Frontend architecture

Eventra's frontend uses Next.js 16. The browser-side API client is
`src/lib/api.js`; its committed development default is
`http://localhost:8080`, overridden only through
`NEXT_PUBLIC_API_BASE_URL`.

The frontend development server runs on `localhost:3000`. The production
build command is deliberately `next build --webpack`: the local delivery
environment supports the Webpack build and does not support Turbopack's
temporary CSS-worker port.

Treat the backend HTTP contract as an external published contract. A change
to routes, payloads, status codes, or security behavior requires an explicitly
assigned cross-repository contract change.
