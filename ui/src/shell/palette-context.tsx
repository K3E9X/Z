import { createContext, useContext } from 'react'

export interface PaletteApi {
  open: (initial?: string) => void
  close: () => void
  isOpen: boolean
  openHelp: () => void
}

export const PaletteContext = createContext<PaletteApi>({
  open: () => undefined,
  close: () => undefined,
  isOpen: false,
  openHelp: () => undefined,
})

export function useCommandPalette(): PaletteApi {
  return useContext(PaletteContext)
}
