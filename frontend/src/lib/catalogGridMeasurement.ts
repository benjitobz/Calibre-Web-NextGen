export interface CatalogGridMeasurement {
  gridTemplateColumns: string;
  gridWidth: number;
  minColumnWidth: number;
}

const RESOLVED_LENGTH = /^(?:0|[1-9]\d*)(?:\.\d+)?px$/;

/**
 * Return the resolved catalog track count only when the grid has a usable
 * content box. A hidden/settling grid can report one genuine 140px track while
 * its own width is zero or near zero; accepting that track is what can strand
 * virtualization at one card per row.
 */
export function measureCatalogColumnCount({
  gridTemplateColumns,
  gridWidth,
  minColumnWidth,
}: CatalogGridMeasurement): number | null {
  if (!Number.isFinite(gridWidth)
    || !Number.isFinite(minColumnWidth)
    || gridWidth <= 0
    || minColumnWidth < 0
    || gridWidth < minColumnWidth) return null;

  const tracks = gridTemplateColumns.trim();
  if (!tracks || tracks === 'none') return null;

  const resolvedTracks = tracks.split(/\s+/);
  return resolvedTracks.every((track) => RESOLVED_LENGTH.test(track))
    ? resolvedTracks.length
    : null;
}
