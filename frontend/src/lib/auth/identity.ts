"use client";

import type { IntegrationIdentity } from "@/lib/contracts/types";

export const IDENTITY_STORAGE_KEY = "wisewe.integration.identity";
export const IDENTITY_CHANGED_EVENT = "wisewe:integration-identity-changed";

let sessionIdentity: IntegrationIdentity | null = null;

function hasBrowserStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

export function getSelectedIdentity(): IntegrationIdentity | null {
  if (sessionIdentity) return sessionIdentity;
  if (!hasBrowserStorage()) return null;

  try {
    const raw = window.localStorage.getItem(IDENTITY_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<IntegrationIdentity>;
    if (!parsed.tenantId || !parsed.userId) return null;
    return {
      tenantId: String(parsed.tenantId),
      userId: String(parsed.userId),
      username: String(parsed.username ?? ""),
      displayName: String(parsed.displayName ?? parsed.username ?? parsed.userId),
      tenantName: String(parsed.tenantName ?? ""),
      roleCodes: Array.isArray(parsed.roleCodes) ? parsed.roleCodes.map(String) : [],
      isTenantAdmin: Boolean(parsed.isTenantAdmin),
      source: String(parsed.source ?? "identity_snapshot"),
    };
  } catch {
    return null;
  }
}

export function setSessionIdentity(identity: IntegrationIdentity | null): void {
  sessionIdentity = identity;
  if (identity && hasBrowserStorage()) {
    window.localStorage.removeItem(IDENTITY_STORAGE_KEY);
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(IDENTITY_CHANGED_EVENT));
  }
}

export function setSelectedIdentity(identity: IntegrationIdentity): void {
  if (!hasBrowserStorage()) return;
  window.localStorage.setItem(IDENTITY_STORAGE_KEY, JSON.stringify(identity));
  window.dispatchEvent(new CustomEvent(IDENTITY_CHANGED_EVENT));
}

export function clearSelectedIdentity(): void {
  sessionIdentity = null;
  if (!hasBrowserStorage()) return;
  window.localStorage.removeItem(IDENTITY_STORAGE_KEY);
  window.dispatchEvent(new CustomEvent(IDENTITY_CHANGED_EVENT));
}

export function getIdentityHeaders(): Record<string, string> {
  if (sessionIdentity) return {};
  const identity = getSelectedIdentity();
  if (!identity) return {};
  return {
    "X-KB-Tenant-Id": identity.tenantId,
    "X-KB-User-Id": identity.userId,
  };
}
