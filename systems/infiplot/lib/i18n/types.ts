import type { Locale } from "./config";

// Translation value type - can be a string or a function that takes parameters
export type TranslationValue = string | ((params: Record<string, string | number>) => string);

// Translation structure - nested objects with translation values at leaves
export type TranslationStructure = Record<string, unknown>;

// Flatten a nested object to dot-notation keys
export type Flatten<T> = T extends object
  ? {
      [K in keyof T]: T[K] extends (...args: any[]) => any
        ? T[K]
        : T[K] extends object
          ? Flatten<T[K]>
          : T[K];
    }[keyof T]
  : T;
