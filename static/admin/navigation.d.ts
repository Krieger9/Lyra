export function draftSession<T>(): { id: string; data: T | null; save(value: T): void; clear(): void };
export function linkWithReturn(destination: string, context?: Record<string, string>): string;
export function renderBreadcrumbs(): void;
