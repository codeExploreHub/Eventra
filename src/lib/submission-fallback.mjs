// Preserve local submission fallback for missing response statuses and server errors.
export function canFallbackToLocalStorage(error) {
  return !error?.status || error?.status >= 500;
}
