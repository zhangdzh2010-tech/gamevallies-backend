import { Injectable, ServiceUnavailableException } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../../prisma/prisma.service';
import {
  DEFAULT_RUNTIME_PROFILE_ID,
  normalizeRuntimeProfileId,
  runtimeProfileLookupCandidates,
} from '../../game/runtime-profile-ids';

type RuntimeProfileSelection = {
  id: string;
  contractSchema?: Prisma.JsonValue | null;
  metadata?: Prisma.JsonValue | null;
};

@Injectable()
export class RuntimeProfileService {
  private static readonly PROMPT_BUNDLE_CACHE_TTL_MS = 30_000;
  private static readonly RUNTIME_PROFILE_CACHE_TTL_MS = 30_000;

  private promptBundleIdentityCache: { value: { id: string; version: number }; cachedAt: number } | null = null;
  private runtimeProfileCache = new Map<string, { value: RuntimeProfileSelection; cachedAt: number }>();

  constructor(private readonly prisma: PrismaService) {}

  async resolveActivePromptBundleIdentity(): Promise<{ id: string; version: number }> {
    const now = Date.now();
    const cached = this.promptBundleIdentityCache;
    if (cached && (now - cached.cachedAt) < RuntimeProfileService.PROMPT_BUNDLE_CACHE_TTL_MS) {
      return cached.value;
    }

    const bundle = await this.prisma.promptBundle.findFirst({
      where: { status: 'active' },
      orderBy: [{ updatedAt: 'desc' }, { version: 'desc' }],
      select: {
        id: true,
        version: true,
      },
    });
    if (!bundle) {
      throw new ServiceUnavailableException('No active prompt bundle is configured');
    }

    this.promptBundleIdentityCache = { value: bundle, cachedAt: now };
    return bundle;
  }

  async resolveRuntimeProfile(profileHint?: string): Promise<RuntimeProfileSelection> {
    const cacheKey = profileHint && profileHint.trim() ? profileHint.trim() : '__default__';
    const now = Date.now();
    const cached = this.runtimeProfileCache.get(cacheKey);
    if (cached && (now - cached.cachedAt) < RuntimeProfileService.RUNTIME_PROFILE_CACHE_TTL_MS) {
      return cached.value;
    }

    const profiles = await this.prisma.runtimeProfileCatalog.findMany({
      where: { enabled: true },
      select: {
        id: true,
        contractSchema: true,
        metadata: true,
      },
      orderBy: [{ updatedAt: 'desc' }, { id: 'asc' }],
    });
    if (profiles.length === 0) {
      throw new ServiceUnavailableException('No enabled runtime profile is configured');
    }

    const defaultProfile = profiles.find((profile) => {
      const metadata = profile.metadata;
      return typeof metadata === 'object' && metadata !== null && (metadata as Record<string, unknown>).default === true;
    });

    const normalizedHint = normalizeRuntimeProfileId(profileHint);
    if (normalizedHint) {
      for (const candidate of runtimeProfileLookupCandidates(normalizedHint)) {
        const hintedProfile = profiles.find((profile) => profile.id === candidate);
        if (hintedProfile) {
          const resolved = {
            ...hintedProfile,
            id: normalizeRuntimeProfileId(hintedProfile.id) || DEFAULT_RUNTIME_PROFILE_ID,
          };
          this.runtimeProfileCache.set(cacheKey, { value: resolved, cachedAt: now });
          return resolved;
        }
      }
    }

    const selected = defaultProfile || profiles[0];
    const resolved = {
      ...selected,
      id: normalizeRuntimeProfileId(selected.id) || DEFAULT_RUNTIME_PROFILE_ID,
    };
    this.runtimeProfileCache.set(cacheKey, { value: resolved, cachedAt: now });
    return resolved;
  }
}
