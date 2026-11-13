/** Tick the exact data fields an LLM may use, out of the chosen datasets' fields. */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { catalog } from '@/api/catalog'
import type { Scope } from '@/api/types'
import { fmt } from '@/lib/format'
import { Button, Checkbox, ErrorNotice, Fieldset, Input, Panel } from '@/ui/kit'

const PAGE = 100

export function FieldsPicker({
  scope,
  datasetIds,
  value,
  onChange,
}: {
  scope: Scope
  datasetIds: string[]
  value: string[]
  onChange: (next: string[]) => void
}) {
  const [search, setSearch] = useState('')
  const query = useQuery({
    queryKey: ['power-pool-lab', 'fields', scope, datasetIds, search],
    enabled: datasetIds.length > 0,
    queryFn: () =>
      catalog.fields(scope, {
        dataset_ids: datasetIds,
        search: search.trim() || null,
        // Least used first: a field few Alphas use is less crowded.
        sort_by: search.trim() ? 'relevance' : 'alpha_count',
        sort_desc: false,
        limit: PAGE,
        offset: 0,
      }),
  })
  const rows = query.data?.results ?? []
  const chosen = new Set(value)
  const toggle = (id: string, on: boolean) =>
    onChange(on ? [...value, id] : value.filter((x) => x !== id))
  const shown = rows.map((r) => r.field_id)

  return (
    <Panel
      title={
        value.length === 0
          ? 'Fields · all of the datasets'
          : `Fields · ${fmt.int(value.length)} chosen`
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-body-compact text-pretty text-ink-subtle">
          Optional. Tick fields to restrict the LLM to them; leave empty to let it use every field
          of the datasets. Least-used fields come first, since fewer Alphas on a field means less
          crowding.
        </p>
        {datasetIds.length === 0 && (
          <p className="text-body-compact text-ink-subtle">Choose datasets first.</p>
        )}
        {datasetIds.length > 0 && (
          <>
            <div className="flex flex-wrap items-end gap-3">
              <Fieldset legend="Search">
                <Input
                  aria-label="Search fields"
                  className="w-64"
                  placeholder="e.g. earnings, dividend, sentiment"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </Fieldset>
              <Button size="sm" onClick={() => onChange([...new Set([...value, ...shown])])}>
                Tick the {fmt.int(shown.length)} shown
              </Button>
              <Button size="sm" disabled={value.length === 0} onClick={() => onChange([])}>
                Clear
              </Button>
            </div>
            {query.isError && <ErrorNotice error={query.error} title="Could not load the fields" />}
            <ul className="max-h-72 divide-y divide-hairline overflow-auto rounded-md border border-hairline">
              {rows.map((r) => (
                <li key={r.field_id} className="flex items-start gap-3 px-3 py-2">
                  <Checkbox
                    aria-label={`Use ${r.field_id}`}
                    label={<span className="num font-medium">{r.field_id}</span>}
                    checked={chosen.has(r.field_id)}
                    onChange={(on) => toggle(r.field_id, on)}
                  />
                  <span className="min-w-0 flex-1 truncate text-body-compact text-ink-subtle">
                    {r.description}
                  </span>
                  <span className="num shrink-0 text-caption text-ink-subtle">
                    {r.field_type} · {fmt.pct(r.coverage)} · {fmt.int(r.alpha_count)} Alphas
                  </span>
                </li>
              ))}
              {rows.length === 0 && !query.isLoading && (
                <li className="px-3 py-2 text-body-compact text-ink-subtle">No fields match.</li>
              )}
            </ul>
            {(query.data?.total ?? 0) > rows.length && (
              <p className="text-caption text-ink-subtle">
                Showing {fmt.int(rows.length)} of {fmt.int(query.data?.total)}. Search to narrow.
              </p>
            )}
          </>
        )}
      </div>
    </Panel>
  )
}
