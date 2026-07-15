import type { CSSProperties } from "react";

export interface TextStyleFFmpeg {
  font_family: string;
  font_size_pct: number;
  color: string;
  stroke_color?: string;
  stroke_width?: number;
  box_color?: string;
  box_padding?: number;
  shadow?: { color: string; dx: number; dy: number; blur: number };
  filter_template?: string;
}

export interface TextStyle {
  id: string;
  name: string;
  preview: string;
  css: CSSProperties;
  animationClass?: string;
  ffmpeg: TextStyleFFmpeg;
}

export const TEXT_STYLES: TextStyle[] = [
  {
    id: "bold_pop",
    name: "Bold Pop",
    preview: "SHOP NOW",
    css: {
      fontFamily: "'Impact', 'Arial Black', sans-serif",
      color: "#ffffff",
      fontSize: "48px",
      WebkitTextStroke: "3px #000000",
      letterSpacing: "0.02em",
    },
    animationClass: "animate-pop-in",
    ffmpeg: {
      font_family: "Impact",
      font_size_pct: 0.08,
      color: "white",
      stroke_color: "black",
      stroke_width: 4,
    },
  },
  {
    id: "neon_glow",
    name: "Neon Glow",
    preview: "LIMITED",
    css: {
      fontFamily: "'Orbitron', monospace",
      color: "#00ffff",
      fontSize: "44px",
      textShadow: "0 0 8px #00ffff, 0 0 16px #00ffff, 0 0 24px #00ffff",
    },
    animationClass: "animate-pulse-glow",
    ffmpeg: {
      font_family: "Monospace",
      font_size_pct: 0.07,
      color: "0x00FFFF",
      shadow: { color: "0x00FFFF", dx: 0, dy: 0, blur: 24 },
    },
  },
  {
    id: "glitch",
    name: "Glitch",
    preview: "ERROR",
    css: {
      fontFamily: "'Space Mono', monospace",
      color: "#ffffff",
      fontSize: "46px",
      textShadow: "-2px 0 #ff0040, 2px 0 #00ffff",
      letterSpacing: "0.05em",
    },
    animationClass: "animate-glitch",
    ffmpeg: {
      font_family: "Monospace",
      font_size_pct: 0.07,
      color: "white",
      filter_template: "pre_render_png",
    },
  },
  {
    id: "chromatic_aberration",
    name: "Chromatic",
    preview: "VIRAL",
    css: {
      fontFamily: "'Inter', sans-serif",
      fontWeight: 800,
      color: "#ffffff",
      fontSize: "46px",
      textShadow: "-3px 0 #ff0000, 3px 0 #00ffff",
    },
    animationClass: "animate-fade-in",
    ffmpeg: {
      font_family: "Sans",
      font_size_pct: 0.07,
      color: "white",
      filter_template: "triple_drawtext_rgb",
    },
  },
  {
    id: "typewriter",
    name: "Typewriter",
    preview: "typing...",
    css: {
      fontFamily: "'Courier New', monospace",
      color: "#00ff41",
      fontSize: "36px",
      backgroundColor: "rgba(0,0,0,0.8)",
      padding: "8px 16px",
      borderRight: "3px solid #00ff41",
    },
    animationClass: "animate-typewriter",
    ffmpeg: {
      font_family: "Monospace",
      font_size_pct: 0.05,
      color: "0x00FF41",
      box_color: "black@0.8",
      box_padding: 8,
    },
  },
  {
    id: "karaoke",
    name: "Karaoke",
    preview: "FOLLOW ME",
    css: {
      fontFamily: "'Poppins', sans-serif",
      fontWeight: 700,
      color: "#ffffff",
      fontSize: "42px",
      background: "linear-gradient(90deg, #FFD700 50%, #ffffff 50%)",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
      backgroundSize: "200% 100%",
    },
    animationClass: "animate-karaoke",
    ffmpeg: {
      font_family: "Sans",
      font_size_pct: 0.06,
      color: "yellow",
      filter_template: "karaoke_highlight",
    },
  },
  {
    id: "kinetic_stack",
    name: "Kinetic Stack",
    preview: "BIG\nSALE",
    css: {
      fontFamily: "'Montserrat', sans-serif",
      fontWeight: 900,
      color: "#ffffff",
      fontSize: "52px",
      lineHeight: "1.1",
      textTransform: "uppercase" as const,
    },
    animationClass: "animate-kinetic-stack",
    ffmpeg: {
      font_family: "Sans",
      font_size_pct: 0.09,
      color: "white",
    },
  },
  {
    id: "tagline_badge",
    name: "Tagline Badge",
    preview: "NEW ARRIVAL",
    css: {
      fontFamily: "'Inter', sans-serif",
      fontWeight: 600,
      color: "#ffffff",
      fontSize: "28px",
      backgroundColor: "#8B82C0",
      padding: "8px 20px",
      borderRadius: "24px",
      letterSpacing: "0.08em",
      textTransform: "uppercase" as const,
    },
    animationClass: "animate-slide-in-bottom",
    ffmpeg: {
      font_family: "Sans",
      font_size_pct: 0.04,
      color: "white",
      box_color: "0x8B82C0@0.9",
      box_padding: 14,
    },
  },
  {
    id: "newspaper",
    name: "Newspaper",
    preview: "BREAKING",
    css: {
      fontFamily: "'Playfair Display', 'Georgia', serif",
      color: "#1a1a1a",
      fontSize: "48px",
      fontWeight: 700,
      backgroundColor: "#f5f0e1",
      padding: "8px 16px",
      borderBottom: "4px double #1a1a1a",
    },
    animationClass: "animate-zoom-in",
    ffmpeg: {
      font_family: "Serif",
      font_size_pct: 0.07,
      color: "0x1A1A1A",
      box_color: "0xF5F0E1@0.95",
      box_padding: 10,
    },
  },
  {
    id: "graffiti",
    name: "Graffiti",
    preview: "FRESH",
    css: {
      fontFamily: "'Permanent Marker', cursive",
      color: "#39FF14",
      fontSize: "52px",
      transform: "rotate(-5deg)",
      textShadow: "3px 3px 0 #000000, -1px -1px 0 #000000",
    },
    animationClass: "animate-bounce-in",
    ffmpeg: {
      font_family: "Permanent Marker",
      font_size_pct: 0.08,
      color: "0x39FF14",
      stroke_color: "black",
      stroke_width: 3,
      filter_template: "pre_render_png",
    },
  },
  {
    id: "shimmer",
    name: "Shimmer",
    preview: "PREMIUM",
    css: {
      fontFamily: "'Inter', sans-serif",
      fontWeight: 800,
      fontSize: "46px",
      background: "linear-gradient(90deg, #FFD700, #FFF8DC, #FFD700, #FFF8DC, #FFD700)",
      backgroundSize: "300% 100%",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
    },
    animationClass: "animate-shimmer-text",
    ffmpeg: {
      font_family: "Sans",
      font_size_pct: 0.07,
      color: "gold",
      filter_template: "pre_render_png",
    },
  },
  {
    id: "liquid",
    name: "Liquid",
    preview: "FLOW",
    css: {
      fontFamily: "'Fredoka', sans-serif",
      color: "#A855F7",
      fontSize: "52px",
      fontWeight: 600,
      filter: "url(#liquid-distortion)",
      textShadow: "0 2px 4px rgba(168,85,247,0.4)",
    },
    animationClass: "animate-liquid",
    ffmpeg: {
      font_family: "Sans",
      font_size_pct: 0.08,
      color: "0xA855F7",
      filter_template: "pre_render_png",
    },
  },
  {
    id: "price_tag",
    name: "Price Tag",
    preview: "$19.99",
    css: {
      fontFamily: "'Roboto Mono', monospace",
      color: "#ffffff",
      fontSize: "40px",
      fontWeight: 700,
      backgroundColor: "#EF4444",
      padding: "6px 20px",
      transform: "rotate(-3deg)",
      clipPath: "polygon(0 0, 100% 0, 95% 100%, 5% 100%)",
    },
    animationClass: "animate-bounce-in",
    ffmpeg: {
      font_family: "Monospace",
      font_size_pct: 0.06,
      color: "white",
      box_color: "red@0.95",
      box_padding: 12,
    },
  },
  {
    id: "countdown",
    name: "Countdown",
    preview: "3",
    css: {
      fontFamily: "'Anton', sans-serif",
      color: "#ffffff",
      fontSize: "80px",
      textShadow: "0 4px 8px rgba(0,0,0,0.5)",
    },
    animationClass: "animate-countdown",
    ffmpeg: {
      font_family: "Sans",
      font_size_pct: 0.12,
      color: "white",
      shadow: { color: "black@0.5", dx: 0, dy: 4, blur: 8 },
    },
  },
  {
    id: "sparkle",
    name: "Sparkle",
    preview: "MAGIC",
    css: {
      fontFamily: "'Poppins', sans-serif",
      color: "#ffffff",
      fontSize: "44px",
      fontWeight: 700,
      textShadow: "0 0 12px rgba(255,215,0,0.6), 0 0 24px rgba(255,215,0,0.3)",
    },
    animationClass: "animate-sparkle",
    ffmpeg: {
      font_family: "Sans",
      font_size_pct: 0.07,
      color: "white",
      shadow: { color: "0xFFD700@0.6", dx: 0, dy: 0, blur: 12 },
      filter_template: "pre_render_png",
    },
  },
];

export function getTextStyleById(id: string): TextStyle | undefined {
  return TEXT_STYLES.find((s) => s.id === id);
}
