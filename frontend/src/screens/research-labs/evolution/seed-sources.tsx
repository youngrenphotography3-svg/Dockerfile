/**
 * Where Auto Select may draw seeds from: which datasets, and which kinds of Alpha.
 *
 * The lists come from the market's own candidates, so a dataset or kind nobody has an Alpha
 * for is never offered. Nothing chosen means no limit; choosing narrows it.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { fmt } from '@/lib/format'
import { Button, Chips, ErrorNotice, Fieldset, Input, Panel, Skeleton } from '@/ui/kit'
import { evolutionLab, type SeedKind } from './api'

/** Datasets listed before a search is needed; chosen ones always show. */
const SHOWN = 40

const KIND_LABELS: Record<SeedKind, { label: string; title: string }> = {
  regular: {
    label: 'Regular',
    title: 'Neither Power Pool nor ATOM: an ordinary Alpha over any datasets.',
  },
  power_pool: {
    label: 'Power Pool',
    title:
      'BRAIN judges it Power Pool eligible; without BRAIN’s check, at most 8 operators and 3 data fields with Sharpe of 1 or more.',
  },
  atom: {
    label: 'ATOM',
    title: 'Every data field comes from one dataset.',
  },
}

export function SeedSources({
  region,
  delay,
  universe,
  datasets,
  kinds,
  onDatasets,
  onKinds,
  disabled,
}: {
  region: string
  delay: number
  universe: string
  datasets: string[]
  kinds: SeedKind[]
  onDatasets: (value: string[]) => void
  onKinds: (value: SeedKind[]) => void
  disabled?: boolean
}) {
  const [search, setSearch] = useState('')
  const sources = useQuery({
    queryKey: ['evolution-lab', 'seed-sources', region, delay, universe],
    queryFn: () => evolutionLab.seedSources({ region, delay, universe }),
    staleTime: 60_000,
  })

  const items = useMemo(() => {
    const all = sources.data?.datasets ?? []
    const needle = search.trim().toLowerCase()
    const matching = needle
      ? all.filter((d) => `${d.datasetId} ${d.name ?? ''}`.toLowerCase().includes(needle))
      : all
    const chosen = new Set(datasets)
    // What is ticked stays on screen whatever the search says, so it can always be unticked.
    const picked = all.filter((d) => chosen.has(d.datasetId))
    const rest = matching.filter((d) => !chosen.has(d.datasetId))
    const shown = [...picked, ...rest.slice(0, SHOWN)]
    return {
      hidden: Math.max(0, rest.length - SHOWN),
      chips: shown.map((d) => ({
        value: d.datasetId,
        title: d.name ?? d.datasetId,
        label: (
          <>
            {d.datasetId} <span className="num text-ink-subtle">{fmt.int(d.alphas)}</span>
          </>
        ),
      })),
    }
  }, [sources.data, search, datasets])

  const counts = new Map((sources.data?.kinds ?? []).map((k) => [k.kind, k.alphas]))
  const restricted = datasets.length > 0 || kinds.length > 0

  return (
    <Panel
      title="Seed Sources"
      bodyClassName="flex flex-col gap-4"
      actions={
        restricted && (
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => {
              onDatasets([])
              onKinds([])
            }}
          >
            Clear
          </Button>
        )
      }
    >
      {sources.isError && <ErrorNotice error={sources.error} title="Could not read the sources" />}
      <p className="text-body-compact text-pretty text-ink-subtle">
        Applies to Auto Select. Nothing chosen means any; an Alpha is taken only when every dataset
        it reads is chosen.
        {sources.data && ` ${fmt.int(sources.data.total)} Alphas can seed here.`}
      </p>
      <Fieldset legend="Kind of Alpha">
        <Chips
          label="Kind of Alpha"
          disabled={disabled}
          items={(Object.keys(KIND_LABELS) as SeedKind[]).map((kind) => ({
            value: kind,
            title: KIND_LABELS[kind].title,
            label: (
              <>
                {KIND_LABELS[kind].label}{' '}
                <span className="num text-ink-subtle">
                  {counts.has(kind) ? fmt.int(counts.get(kind)) : ''}
                </span>
              </>
            ),
          }))}
          value={kinds}
          onChange={onKinds}
        />
      </Fieldset>
      <Fieldset legend="Datasets">
        {sources.isPending ? (
          <Skeleton className="h-9 w-64" />
        ) : (sources.data?.datasets.length ?? 0) === 0 ? (
          <p className="text-body-compact text-ink-subtle">
            No dataset is known for these Alphas. Sync this market in the Data Explorer.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            <Input
              type="search"
              placeholder="Search datasets"
              aria-label="Search datasets"
              value={search}
              disabled={disabled}
              className="max-w-80"
              onChange={(event) => setSearch(event.target.value)}
            />
            <Chips
              label="Datasets"
              disabled={disabled}
              items={items.chips}
              value={datasets}
              onChange={onDatasets}
            />
            {items.hidden > 0 && (
              <span className="text-body-compact text-ink-subtle">
                {fmt.int(items.hidden)} more. Search to narrow them.
              </span>
            )}
          </div>
        )}
      </Fieldset>
    </Panel>
  )
}
