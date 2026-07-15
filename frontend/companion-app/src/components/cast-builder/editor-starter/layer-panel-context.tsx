/**
 * LayerPanelContext — editor-local state for whether the layer ordering
 * panel is visible. Toggled via a Layers button in the toolbar.
 *
 * Phase 2.9.3
 */
import React, { createContext, useContext, useState, useCallback, useMemo } from "react";

interface LayerPanelState {
  isOpen: boolean;
  toggle: () => void;
}

const LayerPanelContext = createContext<LayerPanelState>({
  isOpen: false,
  toggle: () => {},
});

export const useLayerPanel = () => useContext(LayerPanelContext);

export const LayerPanelProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isOpen, setIsOpen] = useState(false);
  const toggle = useCallback(() => setIsOpen((v) => !v), []);
  const value = useMemo(() => ({ isOpen, toggle }), [isOpen, toggle]);

  return (
    <LayerPanelContext.Provider value={value}>
      {children}
    </LayerPanelContext.Provider>
  );
};
