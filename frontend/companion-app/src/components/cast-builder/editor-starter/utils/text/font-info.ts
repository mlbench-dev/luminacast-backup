// Collects font information for the render
// In the editor, this is not used, font info will be loaded dynamically from a backend endpoint

import {FontInfo} from '@remotion/google-fonts';
import {createContext} from 'react';

export type FontInfosContextType = Record<string, FontInfo>;

export const FontInfoContext = createContext<Record<string, FontInfo>>({});
