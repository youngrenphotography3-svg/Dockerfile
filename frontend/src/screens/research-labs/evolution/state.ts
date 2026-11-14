/** The Evolution Lab's choices, kept between visits. */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { DEFAULT_SCOPE } from '@/lib/scope'
import type { SeedKind } from './api'

export interface EvolutionDraft {
  region: string
  delay: number
  universe: string
  seedIds: string[]
  /** `null` until chosen in the form: until then Settings' default applies. */
  cores: number | null
  /** `null` until the user assigns them: a task always has simulations chosen on purpose. */
  simulations: number | null
  /** `null`: sized from the simulations. */
  population: number | null
  mutationRate: number
  /** Empty leaves the lab on its own four group neutralizations. */
  neutralizations: string[]
  /** Datasets Auto Select may take seeds from; empty means any. */
  seedDatasets: string[]
  /** Kinds of Alpha it may take; empty means any. */
  seedKinds: SeedKind[]
  /** The Auto Select job whose result is shown, and the one whose seeds were already taken. */
  autoJobId: string | null
  appliedJobId: string | null
}

export const useEvolutionLab = create<EvolutionDraft>()(
  persist(
    (): EvolutionDraft => ({
      region: DEFAULT_SCOPE.region,
      delay: DEFAULT_SCOPE.delay,
      universe: DEFAULT_SCOPE.universe,
      seedIds: [],
      cores: null,
      simulations: null,
      population: null,
      neutralizations: [],
      mutationRate: 0.05,
      seedDatasets: [],
      seedKinds: [],
      autoJobId: null,
      appliedJobId: null,
    }),
    {
      name: 'alpha-harness-evolution-lab',
      // Version 1 leaves cores unchosen, so Settings' default for new tasks applies.
      // Version 2 adds the seed sources, unrestricted until chosen.
      version: 2,
      migrate: (stored, version) => ({
        ...(stored as EvolutionDraft),
        ...(version < 1 ? { cores: null } : {}),
        seedDatasets: [],
        seedKinds: [],
      }),
    },
  ),
)
