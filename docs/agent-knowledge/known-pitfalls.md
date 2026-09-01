# Frontend known pitfalls

## Wrong backend copy

The nested `Backend/` directory is non-authoritative. Editing it creates a
change that the backend delivery Agent will not own or ship. Use the sibling
`Eventra-Backend` repository.

## Wrong production builder

Replacing the Webpack build with the default Turbopack build can fail in the
local delivery environment because of its temporary CSS-worker port. Preserve
the committed `next build --webpack` command.

## Hidden local configuration

`.env.local` is optional personal input, not delivery evidence. Never rely on,
print, or commit its contents; verify the committed default with the local
contract test.
