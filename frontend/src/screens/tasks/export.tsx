/** Export a task's results as one file for Claude (or anyone) to analyse. */

import { DownloadIcon } from 'lucide-react'
import { toast } from 'sonner'
import { saveText } from '@/lib/download'
import { type ExportFormat, labTasks } from '@/screens/tasks/api'
import { Button } from '@/ui/kit'
import { Menu } from '@/ui/overlay'

const TYPES: Record<ExportFormat, string> = {
  md: 'text/markdown',
  csv: 'text/csv',
  json: 'application/json',
}

function failed(e: unknown) {
  toast.error(e instanceof Error ? e.message : 'Could not export the results.')
}

export function ExportResults({ taskId, disabled }: { taskId: number; disabled?: boolean }) {
  const download = (format: ExportFormat) =>
    labTasks.export(taskId, format).then((text) => {
      saveText(`task-${taskId}-results.${format}`, text, TYPES[format])
    }, failed)
  const copy = () =>
    labTasks
      .export(taskId, 'md')
      .then((text) => navigator.clipboard.writeText(text))
      .then(() => toast.success('Copied the export for Claude'), failed)

  return (
    <Menu
      trigger={
        <Button size="sm" variant="ghost" disabled={disabled}>
          <DownloadIcon />
          Export
        </Button>
      }
      items={[
        { label: 'For Claude (.md)', onClick: () => void download('md') },
        { label: 'Copy for Claude', onClick: () => void copy() },
        { label: 'Spreadsheet (.csv)', onClick: () => void download('csv') },
        { label: 'Everything (.json)', onClick: () => void download('json') },
      ]}
    />
  )
}
