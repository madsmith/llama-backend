// UI preferences remembered in the browser (localStorage, "pref:*" keys).
//
// Storage can be unavailable or throw (private windows, blocked site data,
// quota), so every access is guarded and falls back to the default.

import { type Dispatch, type SetStateAction, useEffect, useState } from "react";

export function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage unavailable; the preference just won't persist
  }
}

/** Converts a preference to and from its stored string. */
export interface PrefCodec<T> {
  /** Return undefined for a missing or unrecognised value to use the default. */
  decode(raw: string): T | undefined;
  encode(value: T): string;
}

export const boolPref: PrefCodec<boolean> = {
  decode: (raw) => (raw === "true" || raw === "1" ? true : raw === "false" || raw === "0" ? false : undefined),
  encode: String,
};

export const stringPref: PrefCodec<string> = {
  decode: (raw) => raw,
  encode: (value) => value,
};

/** A preference restricted to a fixed set of string values. */
export function oneOfPref<T extends string>(values: readonly T[]): PrefCodec<T> {
  return {
    decode: (raw) => (values as readonly string[]).includes(raw) ? (raw as T) : undefined,
    encode: (value) => value,
  };
}

type Init<T> = T | (() => T);

/**
 * useState backed by a stored preference: starts from the saved value (or
 * `initial` when none is saved) and saves every change.
 *
 * Boolean and string defaults pick their codec automatically; pass one for
 * anything else.
 */
export function usePref(key: string, initial: Init<boolean>): [boolean, Dispatch<SetStateAction<boolean>>];
export function usePref(key: string, initial: Init<string>): [string, Dispatch<SetStateAction<string>>];
export function usePref<T>(key: string, initial: Init<T>, codec: PrefCodec<T>): [T, Dispatch<SetStateAction<T>>];
export function usePref<T>(key: string, initial: Init<T>, codec?: PrefCodec<T>): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    const fallback = typeof initial === "function" ? (initial as () => T)() : initial;
    const c = codec ?? codecFor(fallback);
    const raw = readPref(key);
    return (raw !== null ? c.decode(raw) : undefined) ?? fallback;
  });

  useEffect(() => {
    writePref(key, (codec ?? codecFor(value)).encode(value));
  }, [key, value, codec]);

  return [value, setValue];
}

function codecFor<T>(sample: T): PrefCodec<T> {
  if (typeof sample === "boolean") return boolPref as unknown as PrefCodec<T>;
  if (typeof sample === "string") return stringPref as unknown as PrefCodec<T>;
  throw new Error("usePref: pass a codec for non-boolean, non-string preferences");
}
