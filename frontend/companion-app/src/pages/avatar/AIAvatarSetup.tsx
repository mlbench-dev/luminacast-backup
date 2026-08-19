import { useState, useEffect, useRef, useCallback, Fragment } from "react";
import { useNavigate, useSearchParams, useParams } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  Sparkles, Loader2, CheckCircle, ArrowLeft, ArrowRight,
  Wand2, Shuffle, Play, Pause, Lock, RefreshCw, Mic, Volume2, UserCircle,
  Camera, User, Film, Palette, ChevronLeft, ChevronRight, X, RotateCcw,
  Plus, MapPin, BookOpen, Sun, Heart, Zap, Aperture,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { avatarApi, queryClient, userApi } from "@/lib/api";
import { AvatarStatus } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { cn } from "@/lib/cn";
import { confirmAction } from "@/lib/swal";
import { Progress } from "@/components/ui/progress";
import { VoiceCorpusTab } from "@/components/avatar/VoiceCorpusTab";
import { LiveReferenceCard } from "@/components/avatar/LiveReferenceCard";
import { VoiceBrowser } from "@/components/avatar/VoiceBrowser";
import { AvatarIdentityPanel } from "@/components/avatar/AvatarIdentityPanel";
import { ClipMicToggle } from "@/components/avatar/ClipMicToggle";
import { AvatarBackgrounds } from "@/components/avatar/AvatarBackgrounds";
import { STYLE_PRESETS, MAKE_IT_REAL_CHIPS, type StylePresetId } from "@/lib/avatarStyles";
import type { Avatar } from "@/lib/types";

/* ═══ Phase structure — 5 steps (body_description merged into setup) ═══ */
const PHASE_STEPS = [
  { key: "setup", label: "Setup", num: 1 },
  { key: "face", label: "Face", num: 2 },
  { key: "voice", label: "Voice", num: 3 },
  { key: "body_shots", label: "Shots", num: 4 },
  { key: "preview", label: "Preview", num: 5 },
] as const;

type Phase = "setup" | "face" | "voice" | "body_shots" | "preview";

const AVATAR_NAME_MAX_LENGTH = 60;

/* ═══ Icon mapping for style presets ═══ */
const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  camera: Camera, user: User, film: Film, palette: Palette,
  "map-pin": MapPin, sparkles: Sparkles, "book-open": BookOpen,
  sun: Sun, heart: Heart, zap: Zap, aperture: Aperture,
  flower: Heart, // lucide doesn't have "flower", use Heart
};

const RANDOM_HINTS = [
  "cheerful K-pop style influencer",
  "wise older gentleman who reviews watches",
  "futuristic cyberpunk streamer with neon highlights",
  "warm Latina mom who sells kitchen gadgets",
  "cool surfer dude who reviews sunglasses",
  "elegant French woman who sells perfume",
  "nerdy tech reviewer with thick glasses",
  "bubbly anime-inspired virtual idol",
];

const GENDER_OPTIONS = [
  { value: "female", label: "Female", icon: "♀" },
  { value: "male", label: "Male", icon: "♂" },
  { value: "non-binary", label: "Non-binary", icon: "⚧" },
];

const LANGUAGES_WITH_ACCENTS: Record<string, { label: string; accents: { value: string; label: string }[] }> = {
  english: {
    label: "English",
    accents: [
      { value: "us", label: "American" },
      { value: "uk", label: "British" },
      { value: "au", label: "Australian" },
      { value: "ie", label: "Irish" },
      { value: "in", label: "Indian" },
      { value: "za", label: "South African" },
    ],
  },
  spanish: {
    label: "Spanish",
    accents: [
      { value: "es", label: "Spain" },
      { value: "mx", label: "Mexican" },
      { value: "ar", label: "Argentinian" },
      { value: "co", label: "Colombian" },
    ],
  },
  french: {
    label: "French",
    accents: [
      { value: "fr", label: "France" },
      { value: "ca", label: "Canadian" },
    ],
  },
  german: { label: "German", accents: [{ value: "de", label: "Standard" }] },
  portuguese: {
    label: "Portuguese",
    accents: [
      { value: "br", label: "Brazilian" },
      { value: "pt", label: "European" },
    ],
  },
  italian: { label: "Italian", accents: [{ value: "it", label: "Standard" }] },
  chinese: {
    label: "Chinese",
    accents: [
      { value: "zh", label: "Mandarin" },
      { value: "yue", label: "Cantonese" },
    ],
  },
  japanese: { label: "Japanese", accents: [{ value: "jp", label: "Standard" }] },
};

const LANGUAGE_OPTIONS = Object.keys(LANGUAGES_WITH_ACCENTS);

const AGE_RANGES = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"];

const INTEREST_OPTIONS = [
  "Fitness", "Beauty", "Skincare", "Makeup", "Home & Kitchen", "Tech & Gadgets",
  "Fashion", "Outdoor & Camping", "Parenting", "Pets", "Cooking", "Health & Wellness",
  "Supplements", "Books", "Gaming", "Travel", "Crafts", "Sustainability",
  "Luxury", "Budget/Deals", "Moms", "Dads", "Gen Z", "Students",
  "Professionals", "Small Business", "Gardening", "Auto", "Sports", "Music",
  "Entertainment", "Finance", "Education", "Lifestyle",
];

/* ═══ ShimmerField — shared component for auto-generating fields ═══ */

function ShimmerField({ isLoading, children, className }: {
  isLoading: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  if (isLoading) {
    return (
      <div className={cn("relative overflow-hidden rounded-lg border border-border bg-surface", className)}>
        <div className="absolute inset-0 bg-linear-to-r from-transparent via-accent/10 to-transparent animate-shimmer" />
        <div className="p-3 space-y-2">
          <div className="h-4 bg-border/40 rounded w-3/4 animate-pulse" />
          <div className="h-4 bg-border/40 rounded w-1/2 animate-pulse" />
          <div className="h-4 bg-border/40 rounded w-2/3 animate-pulse" />
        </div>
      </div>
    );
  }
  return <>{children}</>;
}

/* ═══ Face Preview Modal ═══ */

function FacePreviewModal({ faces, currentIdx, onClose, onSelect, onNavigate }: {
  faces: string[];
  currentIdx: number;
  onClose: () => void;
  onSelect: (idx: number) => void;
  onNavigate: (idx: number) => void;
}) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") onNavigate(Math.max(0, currentIdx - 1));
      if (e.key === "ArrowRight") onNavigate(Math.min(faces.length - 1, currentIdx + 1));
      if (e.key === "Enter") { onSelect(currentIdx); onClose(); }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [currentIdx, faces.length, onClose, onSelect, onNavigate]);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/90 backdrop-blur-sm flex items-center justify-center p-8"
      onClick={onClose}
      data-testid="face-preview-modal"
    >
      <div onClick={(e: React.MouseEvent) => e.stopPropagation()} className="flex flex-col items-center gap-4">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20 transition"
        >
          <X className="w-6 h-6" />
        </button>
        <button
          onClick={() => onNavigate(currentIdx - 1)}
          disabled={currentIdx === 0}
          className="absolute left-4 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 text-white hover:bg-white/20 transition disabled:opacity-30"
        >
          <ChevronLeft className="w-8 h-8" />
        </button>
        <button
          onClick={() => onNavigate(currentIdx + 1)}
          disabled={currentIdx === faces.length - 1}
          className="absolute right-4 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 text-white hover:bg-white/20 transition disabled:opacity-30"
        >
          <ChevronRight className="w-8 h-8" />
        </button>
        <img
          src={faces[currentIdx]}
          alt={`Face ${currentIdx + 1} of ${faces.length}`}
          className="max-h-[75vh] rounded-2xl"
        />
        <p className="text-white/60 text-sm">Face {currentIdx + 1} of {faces.length}</p>
        <Button size="lg" onClick={() => { onSelect(currentIdx); onClose(); }}>
          Select this face
        </Button>
      </div>
    </div>
  );
}

/* ═══ SetupPhase — ONE scrollable page with Audience + Identity + Body + Continue ═══ */

function SetupPhase({
  avatarId,
  avatarName,
  setAvatarName,
  onContinue,
  initialData,
}: {
  avatarId: string | null;
  avatarName: string;
  setAvatarName: (name: string) => void;
  onContinue: (desc: string, audienceDesc: string, gender: string, bodyDesc: string) => void;
  initialData?: {
    target_audience?: { age_range?: string; interests?: string[]; description?: string };
    description?: string;
    gender?: string;
    body_description?: string;
    style_preset?: string;
    imperfections?: string[];
  };
}) {
  // Audience fields
  const [ageRange, setAgeRange] = useState(initialData?.target_audience?.age_range || "18-24");
  const [interests, setInterests] = useState<string[]>(initialData?.target_audience?.interests || []);
  const [audienceDesc, setAudienceDesc] = useState(initialData?.target_audience?.description || "");
  const [audienceDescOverridden, setAudienceDescOverridden] = useState(!!initialData?.target_audience?.description);
  const [isGeneratingAudienceDesc, setIsGeneratingAudienceDesc] = useState(false);
  const [customInterest, setCustomInterest] = useState("");
  // userInterests = the user's persistent custom presets, fetched from
  // the backend on mount. Adding a custom interest here makes it appear
  // as a clickable preset on every future avatar this user sets up.
  const [userInterests, setUserInterests] = useState<string[]>([]);
  const identityDebounceRef = useRef<NodeJS.Timeout | null>(null);
  const identityReqIdRef = useRef(0);
  useEffect(() => {
    let cancelled = false;
    userApi.getInterests().then((list) => {
      if (!cancelled) setUserInterests(list);
    }).catch(() => { /* offline; harmless */ });
    return () => { cancelled = true; };
  }, []);

  // Avatar description fields
  const [gender, setGender] = useState(initialData?.gender || "");
  const [baseDescription, setBaseDescription] = useState(initialData?.description || "");
  const [bodyDescription, setBodyDescription] = useState(initialData?.body_description || "");
  const [descriptionOverridden, setDescriptionOverridden] = useState(!!initialData?.description);
  const [bodyDescOverridden, setBodyDescOverridden] = useState(!!initialData?.body_description);
  const [nameOverridden, setNameOverridden] = useState(!!avatarName);
  const [selectedPresets, setSelectedPresets] = useState<string[]>(
    initialData?.style_preset ? [initialData.style_preset] : []
  );
  const [selectedChips, setSelectedChips] = useState<string[]>(initialData?.imperfections || []);
  const [isGeneratingIdentity, setIsGeneratingIdentity] = useState(false);

  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const debounceRef = useRef<NodeJS.Timeout | null>(null);
  const autoSaveRef = useRef<NodeJS.Timeout | null>(null);

  // Toggle helpers
  const toggleInterest = (interest: string) => {
    setInterests((prev) =>
      prev.includes(interest) ? prev.filter((i) => i !== interest) : [...prev, interest]
    );
  };

  const addCustomInterest = async () => {
    const trimmed = customInterest.trim();
    if (!trimmed) return;
    setCustomInterest("");
    if (!interests.includes(trimmed)) {
      setInterests((prev) => [...prev, trimmed]);
    }
    // Persist for future avatars. Case-insensitive de-dupe so a built-in
    // preset 'Fitness' isn't shadowed by a typed 'fitness'.
    const lower = trimmed.toLowerCase();
    const alreadyPreset = userInterests.some((p) => p.toLowerCase() === lower);
    if (!alreadyPreset) {
      const next = [...userInterests, trimmed];
      setUserInterests(next);
      try {
        const saved = await userApi.setInterests(next);
        setUserInterests(saved);
      } catch {
        setUserInterests(userInterests);  // roll back on failure
        toast({ title: "Couldn't save custom interest", variant: "destructive" });
      }
    }
  };

  const removeUserInterest = async (label: string) => {
    const lower = label.toLowerCase();
    const next = userInterests.filter((p) => p.toLowerCase() !== lower);
    setUserInterests(next);
    setInterests((prev) => prev.filter((i) => i.toLowerCase() !== lower));
    try {
      await userApi.setInterests(next);
    } catch { /* best-effort */ }
  };

  // Single-select: presets are competing overall visual styles (e.g. "Studio
  // Pro" vs "Natural/Real"), not composable traits — picking more than one
  // sends the AI description generator contradictory instructions. Clicking
  // the already-selected preset clears the selection.
  const togglePreset = (id: string) => {
    setSelectedPresets((prev) => (prev.length === 1 && prev[0] === id ? [] : [id]));
    setDescriptionOverridden(false);
    setBodyDescOverridden(false);
  };

  const toggleChip = (id: string) => {
    setSelectedChips((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]
    );
    setDescriptionOverridden(false);
    setBodyDescOverridden(false);
  };

  // Generate audience description function (called by button)
  const generateAudienceDescription = useCallback(async () => {
    setIsGeneratingAudienceDesc(true);
    try {
      // Parse ageRange string (e.g., "25-34" or "65+") into min/max
      let ageMin = 18;
      let ageMax = 65;
      if (ageRange.includes("-")) {
        const [min, max] = ageRange.split("-").map(Number);
        ageMin = min;
        ageMax = max;
      } else if (ageRange.includes("+")) {
        ageMin = Number(ageRange.replace("+", ""));
        ageMax = 100;
      }

      const data = await avatarApi.aiRewriteAudienceDescription({
        age_min: ageMin,
        age_max: ageMax,
        gender_lean: 50,
        gender_doesnt_matter: true,
        interests,
        geography: "",
        income_bracket: "",
        occupations: [],
      });
      setAudienceDesc(data.description);
      setAudienceDescOverridden(false);
      // Cascade: re-generate avatar identity if gender is set
      // if (gender && !nameOverridden && !descriptionOverridden) {
      //   triggerIdentityGeneration(data.description, gender, selectedPresets, selectedChips);
      // }
    } catch {
      setAudienceDesc(`${ageRange} audience interested in ${interests.join(", ")}`);
    } finally {
      setIsGeneratingAudienceDesc(false);
    }
  }, [ageRange, interests, gender, nameOverridden, descriptionOverridden, selectedPresets, selectedChips]);

  // Single LLM call: returns name + description + body_description.
  // Every caller resets the *Overridden flags immediately before calling
  // this (declaring intent to force a fresh regenerate) — this function
  // used to re-check those same flags here too, but React batches state
  // updates, so the flags this closure sees are still the PRE-click values
  // (the reset hadn't applied yet), silently skipping the very update the
  // caller just asked for. A second click would then see the now-applied
  // reset and work. Always applying the result here removes that stale
  // read and the "have to click twice" bug.
  const triggerIdentityGeneration = async (
    audDesc: string, gen: string, presets: string[], chips: string[], reqId?: number
  ) => {
    if (!audDesc.trim() || !gen) return;
    setIsGeneratingIdentity(true);
    try {
      const data = await avatarApi.aiRewriteAvatarNameAndDescription({
        audience_description: audDesc,
        gender: gen,
        presets,
        imperfections: chips,
      });
      if (reqId !== undefined && reqId !== identityReqIdRef.current) return; // stale — a newer call superseded this one
      setAvatarName(data.name);
      setBaseDescription(data.description);
      if (data.body_description) setBodyDescription(data.body_description);
    } catch (err: any) {
      // Keep current values on failure
      console.error("triggerIdentityGeneration failed:", err);
      toast({ title: "Couldn't generate avatar details", description: err?.response?.data?.detail || "Try again", variant: "destructive" });
    } finally {
      setIsGeneratingIdentity(false);
    }
  };

  // Gender change -> regenerate identity (only fires when user explicitly picks gender)
  const handleGenderChange = (newGender: string) => {
    setGender(newGender);
    setNameOverridden(false);
    setDescriptionOverridden(false);
    setBodyDescOverridden(false);
    const audDesc = audienceDesc || `${ageRange} audience interested in ${interests.join(", ")}`;
    void triggerIdentityGeneration(audDesc, newGender, selectedPresets, selectedChips);
  };

  // Preset/chip toggle -> regenerate description
  // useEffect(() => {
  //   if (!audienceDesc.trim() || !gender || (descriptionOverridden && bodyDescOverridden)) return;
  //   if (identityDebounceRef.current) clearTimeout(identityDebounceRef.current);
  //   const reqId = ++identityReqIdRef.current;
  //   identityDebounceRef.current = setTimeout(() => {
  //     triggerIdentityGeneration(audienceDesc, gender, selectedPresets, selectedChips, reqId);
  //   }, 600);
  //   return () => { if (identityDebounceRef.current) clearTimeout(identityDebounceRef.current); };
  // }, [gender, selectedPresets.join(","), selectedChips.join(",")]);

  // Inspire Me — randomize
  const handleInspireMe = async () => {
    const randomGender = GENDER_OPTIONS[Math.floor(Math.random() * GENDER_OPTIONS.length)].value;
    const randomPresets = [STYLE_PRESETS[Math.floor(Math.random() * STYLE_PRESETS.length)].id];
    const randomChips = MAKE_IT_REAL_CHIPS
      .filter(() => Math.random() > 0.7)
      .map((c) => c.id)
      .slice(0, 3);

    setGender(randomGender);
    setSelectedPresets(randomPresets);
    setSelectedChips(randomChips);
    setNameOverridden(false);
    setDescriptionOverridden(false);
    setBodyDescOverridden(false);

    const audDesc = audienceDesc || `${ageRange} audience interested in ${interests.join(", ")}`;
    await triggerIdentityGeneration(audDesc, randomGender, randomPresets, randomChips);
  };

  // Debounced auto-save (800ms after any field change)
  useEffect(() => {
    if (!avatarId) return;
    if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    autoSaveRef.current = setTimeout(async () => {
      setSaveStatus("saving");
      try {
        await avatarApi.aiSaveSetup(avatarId, {
          target_audience: {
            age_range: ageRange,
            interests,
            description: audienceDesc,
          },
          name: avatarName,
          description: baseDescription,
          gender,
          body_description: bodyDescription,
          style_preset: selectedPresets[0] || undefined,
          imperfections: selectedChips,
        });
        setSaveStatus("saved");
        setTimeout(() => setSaveStatus("idle"), 2000);
      } catch {
        setSaveStatus("idle");
      }
    }, 800);
    return () => { if (autoSaveRef.current) clearTimeout(autoSaveRef.current); };
  }, [avatarId, ageRange, interests.join(","), audienceDesc, avatarName, baseDescription, gender, bodyDescription, selectedPresets.join(","), selectedChips.join(",")]);

  // Continue — save explicitly + navigate
  const handleContinue = async () => {
    if (!avatarId) return;
    setSaving(true);
    try {
      await avatarApi.aiSaveSetup(avatarId, {
        target_audience: {
          age_range: ageRange,
          interests,
          description: audienceDesc,
        },
        name: avatarName,
        description: baseDescription,
        gender,
        body_description: bodyDescription,
        style_preset: selectedPresets[0] || undefined,
        imperfections: selectedChips,
      });
      onContinue(baseDescription, audienceDesc, gender, bodyDescription);
    } catch (err: any) {
      toast({ title: "Failed to save setup", description: err?.response?.data?.detail || "Try again", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const canContinue = audienceDesc.trim() && avatarName.trim() && baseDescription.trim() && gender;

  return (
    <div className="space-y-8">
      {/* Save indicator */}
      <div className="flex items-center justify-end text-xs text-text-muted h-4">
        {saveStatus === "saving" && <><Loader2 className="h-3 w-3 animate-spin mr-1" /> Saving...</>}
        {saveStatus === "saved" && <><CheckCircle className="h-3 w-3 text-success mr-1" /> Saved</>}
      </div>

      {/* ── SECTION 1: Target Audience ── */}
      <div className="space-y-5">
        <div>
          <h3 className="text-lg font-semibold text-text mb-1">Target Audience</h3>
          <p className="text-sm text-text-muted">Who are you creating content for?</p>
        </div>

        {/* Age range pills */}
        <div>
          <label className="text-sm font-medium text-text mb-2 block">Age range</label>
          <div className="flex flex-wrap gap-2">
            {AGE_RANGES.map((range) => (
              <button
                key={range}
                onClick={() => setAgeRange(range)}
                className={cn(
                  "rounded-full px-3 py-1 text-sm font-medium transition-all",
                  ageRange === range
                    ? "text-white shadow-xs"
                    : "bg-surface text-text-muted hover:text-text border border-border"
                )}
                style={ageRange === range ? { backgroundColor: "var(--accent-active)" } : undefined}
              >
                {range}
              </button>
            ))}
          </div>
        </div>

        {/* Interests multi-select */}
        <div>
          <label className="text-sm font-medium text-text mb-2 block">Interests</label>
          {/* Built-in presets, then this user's saved custom presets. Custom
              chips get a small × to remove them from the user-wide preset list.
              Case-insensitive de-dupe so 'Fitness' and 'fitness' don't both
              appear if the user typed one before this UI shipped. */}
          <div className="flex flex-wrap gap-1.5">
            {INTEREST_OPTIONS.map((interest) => (
              <button
                key={interest}
                onClick={() => toggleInterest(interest)}
                className={cn(
                  "rounded-full px-2.5 py-1 text-xs font-medium transition-all",
                  interests.includes(interest)
                    ? "text-white shadow-xs"
                    : "bg-surface text-text-muted hover:text-text border border-border"
                )}
                style={interests.includes(interest) ? { backgroundColor: "var(--accent-active)" } : undefined}
              >
                {interest}
              </button>
            ))}
            {userInterests
              .filter((p) => !INTEREST_OPTIONS.some((b) => b.toLowerCase() === p.toLowerCase()))
              .map((interest) => {
                const selected = interests.some((i) => i.toLowerCase() === interest.toLowerCase());
                return (
                  <span
                    key={`u-${interest}`}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full pl-2.5 pr-1.5 py-1 text-xs font-medium transition-all",
                      selected
                        ? "text-white shadow-xs"
                        : "bg-surface text-text-muted hover:text-text border border-border",
                    )}
                    style={selected ? { backgroundColor: "var(--accent-active)" } : undefined}
                  >
                    <button type="button" onClick={() => toggleInterest(interest)} className="focus:outline-none">
                      {interest}
                    </button>
                    <button
                      type="button"
                      aria-label={`Remove ${interest}`}
                      title="Remove from your saved interests"
                      onClick={() => removeUserInterest(interest)}
                      className="ml-0.5 inline-flex items-center justify-center w-4 h-4 rounded-full text-text-muted/70 hover:text-text hover:bg-white/10"
                    >
                      ×
                    </button>
                  </span>
                );
              })}
          </div>
          <div className="flex gap-2 mt-2">
            <Input
              placeholder="Add custom interest..."
              value={customInterest}
              onChange={(e) => setCustomInterest(e.target.value)}
              className="text-xs flex-1"
              onKeyDown={(e) => e.key === "Enter" && addCustomInterest()}
            />
            <Button variant="outline" size="sm" onClick={addCustomInterest} disabled={!customInterest.trim()}>
              <Plus className="h-3 w-3" />
            </Button>
          </div>
        </div>

        {/* Audience description */}
        <div>
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium text-text mb-2 block">Describe your audience</label>
            <Button
              size="sm"
              variant="outline"
              onClick={generateAudienceDescription}
              disabled={isGeneratingAudienceDesc}
            >
              {isGeneratingAudienceDesc ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Wand2 className="h-3 w-3" />
              )}
              {isGeneratingAudienceDesc ? "Generating..." : "Generate"}
            </Button>
          </div>
          <ShimmerField isLoading={isGeneratingAudienceDesc} className="min-h-[80px]">
            <textarea
              value={audienceDesc}
              onChange={(e) => { setAudienceDesc(e.target.value); setAudienceDescOverridden(true); }}
              placeholder="Click 'Generate' to create an audience description..."
              className="w-full rounded-lg border border-border bg-surface p-3 text-sm text-text placeholder:text-text-muted/50 min-h-[80px] focus:border-accent focus:outline-hidden"
              maxLength={500}
              data-testid="audience-description-field"
            />
          </ShimmerField>
        </div>
      </div>

      {/* ── SECTION 2: Avatar Identity Card ── */}
      <div className="space-y-5">
        <div>
          <h3 className="text-lg font-semibold text-text mb-1">
            {avatarName ? `Meet ${avatarName}` : "Avatar Identity Card"}
          </h3>
          <p className="text-sm text-text-muted">Design your avatar's look and personality.</p>
        </div>

        {/* Gender */}
        <div>
          <label className="text-sm font-medium text-text mb-2 block">Gender</label>
          <div className="flex gap-2">
            {GENDER_OPTIONS.map((g) => (
              <button
                key={g.value}
                onClick={() => handleGenderChange(g.value)}
                className={cn(
                  "flex items-center gap-1.5 rounded-full px-4 py-2 text-sm font-medium transition-all",
                  gender === g.value
                    ? "text-white shadow-xs"
                    : "bg-surface border border-border text-text-muted hover:border-accent/40",
                )}
                style={gender === g.value ? { backgroundColor: "var(--accent-active)" } : undefined}
                data-testid={`gender-${g.value}`}
              >
                <span className="text-sm">{g.icon}</span> {g.label}
              </button>
            ))}
          </div>
        </div>

        {/* Style Presets — 3x4 grid of 12 */}
        <div>
          <label className="text-sm font-medium text-text mb-2 block">Style Preset</label>
          <div className="grid grid-cols-3 gap-2">
            {STYLE_PRESETS.map((preset) => {
              const Icon = ICON_MAP[preset.icon] || Sparkles;
              const isActive = selectedPresets.includes(preset.id);
              return (
                <button
                  key={preset.id}
                  onClick={() => togglePreset(preset.id)}
                  className={cn(
                    "flex items-center gap-2.5 rounded-lg border-2 p-2.5 text-left transition-all",
                    isActive
                      ? "ring-1 ring-(--accent-active)/20"
                      : "border-border bg-bg hover:border-accent/40",
                  )}
                  style={isActive ? {
                    borderColor: "var(--accent-active)",
                    backgroundColor: "var(--accent-active-muted)",
                  } : undefined}
                  data-testid={`style-preset-${preset.id}`}
                >
                  <div className={cn(
                    "flex h-7 w-7 items-center justify-center rounded-full shrink-0",
                    isActive ? "bg-(--accent-active)/20" : "bg-surface",
                  )}>
                    <Icon className={cn("h-3.5 w-3.5", isActive ? "text-(--accent-active)" : "text-text-muted")} />
                  </div>
                  <div className="min-w-0">
                    <p className={cn("text-xs font-medium truncate", isActive ? "text-(--accent-active)" : "text-text")}>{preset.label}</p>
                    <p className="text-[10px] text-text-muted truncate">{preset.subtitle}</p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Make it real chips — 16 items */}
        <div>
          <label className="text-sm font-medium text-text mb-2 block">Make it real — add human details</label>
          <div className="flex flex-wrap gap-1.5">
            {MAKE_IT_REAL_CHIPS.map((chip) => {
              const isActive = selectedChips.includes(chip.id);
              return (
                <button
                  key={chip.id}
                  onClick={() => toggleChip(chip.id)}
                  className={cn(
                    "rounded-full px-2.5 py-1 text-[11px] font-medium transition-all",
                    isActive
                      ? "text-white shadow-xs"
                      : "bg-surface text-text-dim hover:text-text border border-border/80 hover:border-accent/40"
                  )}
                  style={isActive ? { backgroundColor: "var(--accent-active)" } : undefined}
                  data-testid={`chip-${chip.id}`}
                >
                  {chip.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Avatar description */}
        <div>
          <label className="text-sm font-medium text-text mb-2 block">Avatar description</label>
          <ShimmerField isLoading={isGeneratingIdentity && !descriptionOverridden} className="min-h-[120px]">
            <textarea
              value={baseDescription}
              onChange={(e) => { setBaseDescription(e.target.value); setDescriptionOverridden(true); }}
              placeholder={gender ? "Auto-generated from audience + gender + presets..." : "Pick a gender to auto-generate"}
              className="w-full rounded-lg border border-border bg-surface p-3 text-sm text-text placeholder:text-text-muted/50 min-h-[120px] focus:border-accent focus:outline-hidden"
              maxLength={1000}
              data-testid="avatar-description-field"
            />
          </ShimmerField>
        </div>

        {/* Body description */}
        <div>
          <label className="text-sm font-medium text-text mb-2 block">Body description</label>
          <ShimmerField isLoading={isGeneratingIdentity && !bodyDescOverridden} className="min-h-[100px]">
            <textarea
              value={bodyDescription}
              onChange={(e) => { setBodyDescription(e.target.value); setBodyDescOverridden(true); }}
              placeholder={gender ? "Auto-generated for consistent full-body shots..." : "Pick a gender to auto-generate"}
              className="w-full rounded-lg border border-border bg-surface p-3 text-sm text-text placeholder:text-text-muted/50 min-h-[100px] focus:border-accent focus:outline-hidden"
              maxLength={1000}
              data-testid="body-description-field"
            />
          </ShimmerField>
        </div>

        {/* Avatar name — last field. Auto-generated from audience + gender + presets
            + chips + descriptions once the mandatory inputs are filled, but the
            user can edit it at any point. */}
        <div>
          <label className="text-sm font-medium text-text mb-2 block">Avatar name</label>
          <ShimmerField isLoading={isGeneratingIdentity && !nameOverridden} className="h-10">
            <Input
              value={avatarName}
              onChange={(e) => { setAvatarName(e.target.value.slice(0, AVATAR_NAME_MAX_LENGTH)); setNameOverridden(true); }}
              placeholder={gender ? "Generating name..." : "Fill the fields above first"}
              className="text-lg font-semibold"
              maxLength={AVATAR_NAME_MAX_LENGTH}
              data-testid="avatar-name-field"
            />
          </ShimmerField>
          <p className="text-[10px] text-text-muted mt-1 text-right">
            {avatarName.length}/{AVATAR_NAME_MAX_LENGTH}
          </p>
        </div>

        {/* Action buttons */}
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleInspireMe} disabled={isGeneratingIdentity} className="cursor-pointer">
            {isGeneratingIdentity ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Shuffle className="h-4 w-4 mr-2" />}
            Inspire Me
          </Button>
          <Button
            className="cursor-pointer"
            variant="outline"
            onClick={() => {
              setNameOverridden(false);
              setDescriptionOverridden(false);
              setBodyDescOverridden(false);
              triggerIdentityGeneration(audienceDesc, gender, selectedPresets, selectedChips);
            }}
            disabled={isGeneratingIdentity || !gender || !audienceDesc.trim()}
            title={!gender ? "Pick a gender first" : !audienceDesc.trim() ? "Describe your audience first" : undefined}
          >
            <Wand2 className={cn("h-4 w-4 mr-2", isGeneratingIdentity && "animate-spin")} />
            {baseDescription.trim() ? "Regenerate" : "Generate"}
          </Button>
        </div>
      </div>

      {/* ── SECTION 3: Continue ── */}
      <Button
        onClick={handleContinue}
        disabled={saving || !canContinue}
        className="w-full"
        size="lg"
        data-testid="setup-continue-btn"
      >
        {saving ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
        Continue to Face Generation
        <ArrowRight className="h-4 w-4 ml-2" />
      </Button>
    </div>
  );
}

/* ═══ FacePhase — goes straight to generation + gallery (no "Give me a spark", no "Meet [Name]") ═══ */

function FacePhase({
  avatarId,
  avatarName,
  description,
  setDescription,
  avatar,
  onContinueToVoice,
}: {
  avatarId: string | null;
  avatarName: string;
  description: string;
  setDescription: (next: string) => void;
  avatar?: Avatar;
  onContinueToVoice: (faceUrl: string) => void;
}) {
  const [faces, setFaces] = useState<string[]>([]);
  const [selectedFaceIdx, setSelectedFaceIdx] = useState<number | null>(null);
  const [editPrompt, setEditPrompt] = useState("");
  const [editVersions, setEditVersions] = useState<string[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [loadedFaces, setLoadedFaces] = useState<Set<number>>(new Set());
  const [modalFaceIdx, setModalFaceIdx] = useState<number | null>(null);

  const [isGeneratingFaces, setIsGeneratingFaces] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  // Auto-generate faces on mount using the description from Setup
  useEffect(() => {
    if (!description.trim() || !avatarId) return;
    handleGenerateFaces();
  }, []);

  const handleGenerateFaces = async () => {
    if (!description.trim() || !avatarId) return;
    setIsGeneratingFaces(true);
    setFaces([]);
    setSelectedFaceIdx(null);
    setLoadedFaces(new Set());
    setEditVersions([]);
    setSelectedVersion(null);

    // Persist the (possibly edited) description back to the avatar record
    // so it survives a navigate-away / page reload, and so subsequent
    // generations use the latest text. Failure here is non-fatal — the
    // generation still uses the description in the request body.
    try {
      await avatarApi.updateAvatar(avatarId, { description: description.trim() });
      queryClient.invalidateQueries({ queryKey: ["avatar-identity", avatarId] });
    } catch (err) {
      console.warn("updateAvatar(description) failed; continuing with face gen", err);
    }

    try {
      const data = await avatarApi.aiGenerateFaces(avatarId, { description: description.trim() });
      setFaces(data.face_urls);
    } catch (err: any) {
      toast({ title: "Face generation failed", description: err?.response?.data?.detail || "Try again", variant: "destructive" });
    } finally {
      setIsGeneratingFaces(false);
    }
  };

  const handleEditFace = async () => {
    if (selectedFaceIdx === null || !editPrompt.trim()) return;
    const faceUrl = selectedVersion !== null ? editVersions[selectedVersion] : faces[selectedFaceIdx];
    setIsEditing(true);
    try {
      const data = await avatarApi.aiEditFace(avatarId!, { face_url: faceUrl, instructions: editPrompt.trim() });
      setEditVersions((prev) => [...prev, data.edited_url]);
      setSelectedVersion(editVersions.length);
      setEditPrompt("");
    } catch {
      toast({ title: "Edit failed", variant: "destructive" });
    } finally {
      setIsEditing(false);
    }
  };

  const handleContinue = async () => {
    const faceUrl = selectedVersion !== null ? editVersions[selectedVersion] : (selectedFaceIdx !== null ? faces[selectedFaceIdx] : null);
    if (!faceUrl || !avatarId) return;

    try {
      await avatarApi.aiSelectFace(avatarId, { face_url: faceUrl });
      queryClient.invalidateQueries({ queryKey: ["avatar-identity", avatarId] });
    } catch { }

    onContinueToVoice(faceUrl);
  };

  // Live preview for the Identity panel: whatever the user is currently
  // looking at on the right (selected edit version > selected thumbnail)
  // should appear in the left panel immediately, before persistence.
  const previewFaceUrl =
    selectedVersion !== null && editVersions[selectedVersion]
      ? editVersions[selectedVersion]
      : selectedFaceIdx !== null && faces[selectedFaceIdx]
        ? faces[selectedFaceIdx]
        : undefined;

  return (
    <div className="flex gap-6">
      {/* Identity panel — left column */}
      {avatar && (
        <div className="hidden lg:block w-[320px] shrink-0">
          <AvatarIdentityPanel
            avatar={avatar}
            className="sticky top-4"
            faceUrlOverride={previewFaceUrl}
            descriptionOverride={description}
          />
        </div>
      )}
      <div className="flex-1 min-w-0 space-y-5">
        {/* Generation prompt: editable so the user can refine the description
          BEFORE clicking Regenerate. Edits persist back to the parent state
          (and to the avatar record on Regenerate). The textarea autosizes
          up to ~5 lines so a long description is comfortable to edit. */}
        <div className="flex items-start gap-3 rounded-lg border border-border bg-bg/50 p-3">
          <div className="flex-1 min-w-0 space-y-1">
            <p className="text-[10px] text-text-muted uppercase tracking-wide">
              Description for {avatarName || "your avatar"}
            </p>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe the avatar's appearance — the model uses this to generate the 8 faces below."
              rows={3}
              className="w-full resize-y rounded-md border border-border/50 bg-background/60 px-2 py-1.5 text-xs text-text placeholder:text-text-muted leading-relaxed focus:outline-none focus:ring-1 focus:ring-accent/40"
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 self-start mt-5"
            onClick={handleGenerateFaces}
            disabled={isGeneratingFaces || !description.trim()}
            title={!description.trim() ? "Add a description first" : "Generate 8 new faces from this description"}
          >
            <RefreshCw className={cn("mr-1 h-3 w-3", isGeneratingFaces && "animate-spin")} /> Regenerate
          </Button>
        </div>

        {isGeneratingFaces && (
          <div className="flex flex-col items-center justify-center py-12 space-y-3">
            <Loader2 className="h-8 w-8 animate-spin text-accent" />
            <span className="text-text-muted">Generating 8 faces...</span>
          </div>
        )}

        {!isGeneratingFaces && faces.length > 0 && (
          <>
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-text">Pick your avatar's face</h2>
              <div className="flex items-center gap-3">
                <span className="text-xs text-text-muted">{faces.length} faces generated</span>
                {/* F4: explicit regenerate of the whole face grid. Confirms before
                  wiping current selections + edit history. */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={async () => {
                    if (await confirmAction({
                      title: "Regenerate all 8 faces?",
                      text: "Current selections will be replaced.",
                      confirmButtonText: "Regenerate",
                    })) {
                      handleGenerateFaces();
                    }
                  }}
                  disabled={isGeneratingFaces || !description.trim()}
                  title="Generate a fresh batch of 8 faces from the current description"
                  data-testid="regenerate-faces-grid"
                >
                  <Sparkles className={cn("mr-1 h-3 w-3", isGeneratingFaces && "animate-spin")} /> Regenerate faces
                </Button>
              </div>
            </div>

            <div className="grid grid-cols-4 gap-3">
              {faces.map((url, i) => (
                <button
                  key={i}
                  onClick={() => {
                    // F5: re-selecting the already-selected tile re-focuses the
                    // edit panel (scrolls into view) and resets the prompt input
                    // so the user can keep iterating; do NOT clear edit history
                    // — the Versions strip stays so the user has full control.
                    if (selectedFaceIdx === i) {
                      setEditPrompt("");
                      requestAnimationFrame(() => {
                        document.querySelector('[data-testid="face-edit-panel"]')?.scrollIntoView({ behavior: "smooth", block: "center" });
                      });
                    } else {
                      setModalFaceIdx(i);
                    }
                  }}
                  className={cn(
                    "group relative rounded-xl overflow-hidden border-2 transition-all",
                    selectedFaceIdx === i ? "border-accent ring-2 ring-accent/30 scale-[1.02]" : "border-border hover:border-accent/40",
                  )}
                  style={{ aspectRatio: "3/4", minHeight: 180 }}
                  data-testid={`face-candidate-${i}`}
                  data-selected={selectedFaceIdx === i ? "true" : undefined}
                >
                  {!loadedFaces.has(i) && (
                    <div className="absolute inset-0 bg-border/30 animate-pulse" />
                  )}
                  {/* F1: slow zoom-out on hover. After ~300ms hover delay the
                    image scales from 1.0 to ~0.92 over ~3s ease-out so the
                    user can see the full frame comfortably. transition-delay
                    only applies on hover-in; on hover-out it snaps back. */}
                  <img
                    src={url}
                    alt={`Face ${i + 1}`}
                    className={cn(
                      "w-full h-full object-cover transition-opacity duration-500",
                      loadedFaces.has(i) ? "opacity-100" : "opacity-0",
                      "transform group-hover:scale-[0.92] [transition:transform_3000ms_cubic-bezier(0.22,1,0.36,1)_300ms,opacity_500ms_ease]",
                    )}
                    onLoad={() => setLoadedFaces((prev) => new Set(prev).add(i))}
                  />
                  {selectedFaceIdx === i && (
                    <div className="absolute top-1.5 right-1.5">
                      <CheckCircle className="h-5 w-5 text-white drop-shadow-lg" />
                    </div>
                  )}
                  <div className="absolute bottom-1.5 left-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">
                    #{i + 1}
                  </div>
                </button>
              ))}
            </div>

            {modalFaceIdx !== null && faces[modalFaceIdx] && (
              <FacePreviewModal
                faces={faces}
                currentIdx={modalFaceIdx}
                onClose={() => setModalFaceIdx(null)}
                onSelect={(idx) => { setSelectedFaceIdx(idx); setEditVersions([]); setSelectedVersion(null); }}
                onNavigate={setModalFaceIdx}
              />
            )}

            {selectedFaceIdx !== null && faces[selectedFaceIdx] && (
              <div className="rounded-xl border border-border bg-surface p-5 space-y-4" data-testid="face-edit-panel">
                <div className="flex gap-5">
                  <div className="shrink-0 group">
                    {/* F1: slow zoom-out on hover for the main selected preview. */}
                    <img
                      src={selectedVersion !== null ? editVersions[selectedVersion] : faces[selectedFaceIdx]}
                      alt="Selected"
                      className="rounded-xl object-cover border-2 border-accent transform group-hover:scale-[0.92] [transition:transform_3000ms_cubic-bezier(0.22,1,0.36,1)_300ms]"
                      style={{ width: 256, height: 340, minWidth: 256, minHeight: 256 }}
                    />
                    {editVersions.length > 0 && selectedVersion !== null && (
                      <p className="mt-1.5 text-center text-[10px] text-accent">Edited</p>
                    )}
                  </div>

                  <div className="flex-1 space-y-4">
                    <div>
                      <p className="text-xs font-medium text-text mb-2">Edit this face</p>
                      <div className="flex gap-2">
                        <Input
                          placeholder="add glasses, change hair to blonde..."
                          value={editPrompt}
                          onChange={(e) => setEditPrompt(e.target.value)}
                          className="text-xs"
                          onKeyDown={(e) => e.key === "Enter" && handleEditFace()}
                        />
                        <Button size="sm" disabled={isEditing || !editPrompt.trim()} onClick={handleEditFace}>
                          {isEditing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
                        </Button>
                      </div>
                    </div>

                    {editVersions.length > 0 && (
                      <div>
                        <p className="text-[10px] text-text-muted mb-1.5">Versions</p>
                        <div className="flex gap-2 overflow-x-auto">
                          <button
                            onClick={() => setSelectedVersion(null)}
                            className={cn(
                              "shrink-0 rounded-lg overflow-hidden border-2 w-14 h-18",
                              selectedVersion === null ? "border-accent" : "border-border hover:border-accent/40",
                            )}
                          >
                            <img src={faces[selectedFaceIdx]} alt="Original" className="w-full h-full object-cover" />
                          </button>
                          {editVersions.map((url, i) => (
                            <button
                              key={i}
                              onClick={() => setSelectedVersion(i)}
                              className={cn(
                                "shrink-0 rounded-lg overflow-hidden border-2 w-14 h-18",
                                selectedVersion === i ? "border-accent" : "border-border hover:border-accent/40",
                              )}
                            >
                              <img src={url} alt={`Edit ${i + 1}`} className="w-full h-full object-cover" />
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex gap-2">
                  <Button onClick={handleContinue} className="flex-1">
                    Continue to voice <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            )}

            {/* F2: name-able background photos. Each tile shows the image, has an
              inline-editable name (click to rename, blur or Enter to save),
              and supports upload + AI-generation. The backend already exposes
              GET/POST/PATCH/DELETE on /avatars/{id}/backgrounds. */}
            {avatarId && (
              <div className="rounded-xl border border-border bg-surface p-5">
                <p className="text-xs text-text-muted mb-2">
                  Optional: upload or generate scene photos for this avatar. Click a name to rename it (max 80 chars).
                </p>
                <AvatarBackgrounds avatarId={avatarId} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ═══ VoicePhase sub-component ═══ */

function VoicePhase({
  avatarId,
  avatarName,
  description,
  lockedFaceUrl,
  avatar,
  onLockVoice,
  onBackToFace,
}: {
  avatarId: string;
  avatarName: string;
  description: string;
  lockedFaceUrl: string | null;
  avatar?: Avatar;
  onLockVoice: (testSpeech: string) => void;
  onBackToFace: () => void;
}) {
  const [voiceScreen, setVoiceScreen] = useState<"description" | "previews">("description");
  const [voiceDescription, setVoiceDescription] = useState("");
  const [testSpeech, setTestSpeech] = useState("");
  // Initialize gender from the avatar's profile (set in Setup) so the voice
  // step starts in sync. Without this the chip defaulted to "female" even
  // when the user picked Male in Setup, which leaked into voice generation.
  const [gender, setGender] = useState((avatar?.gender as string) || "female");
  const [language, setLanguage] = useState("english");
  const [accent, setAccent] = useState("us");
  // Track whether the user has hand-edited the voice description; if they
  // have, we don't auto-regenerate from chip changes (we'd clobber their text).
  const [voiceDescOverridden, setVoiceDescOverridden] = useState(false);
  const voiceDescDebounceRef = useRef<NodeJS.Timeout | null>(null);
  const [showVoiceBrowser, setShowVoiceBrowser] = useState(false);
  const [voicePreviews, setVoicePreviews] = useState<Array<{ preview_id: string; audio_url: string; index: number }>>([]);
  const [selectedPreviewIdx, setSelectedPreviewIdx] = useState<number | null>(null);

  const [isGeneratingVoiceDesc, setIsGeneratingVoiceDesc] = useState(false);
  const [isGeneratingVoicePreviews, setIsGeneratingVoicePreviews] = useState(false);
  const [isApprovingVoice, setIsApprovingVoice] = useState(false);

  const [playingIdx, setPlayingIdx] = useState<number | null>(null);
  const audioRefs = useRef<(HTMLAudioElement | null)[]>([]);

  // Generate (or regenerate) the voice description so it reflects the
  // user's current base-voice picks (gender + language + accent). Triggered
  // on mount and from the explicit "Regenerate" button below.
  // - When the user hasn't picked anything yet, the request still sends
  //   the form's defaults so the description matches what's on screen.
  // - On regenerate, the latest picks are sent and the suggested_filters
  //   echoed back are re-applied (the backend defaults them to the request
  //   values when the LLM omits them).
  const handleGenerateDescription = useCallback(async () => {
    setIsGeneratingVoiceDesc(true);
    try {
      const data = await avatarApi.aiGenerateVoiceDescription(avatarId, {
        gender,
        language,
        accent,
      });
      setVoiceDescription(data.voice_description);
      setTestSpeech(data.test_speech);
      if (data.suggested_filters?.gender) setGender(data.suggested_filters.gender);
      if (data.suggested_filters?.language) setLanguage(data.suggested_filters.language);
    } catch {
      setVoiceDescription("Warm, friendly voice with clear pronunciation and natural energy");
      setTestSpeech(`Hi everyone! I'm ${avatarName}, and I'm so excited to show you some amazing products today!`);
    } finally {
      setIsGeneratingVoiceDesc(false);
    }
  }, [avatarId, gender, language, accent, avatarName]);

  // Auto-generate voice description on mount AND whenever gender/language/
  // accent change (debounced 500ms). The user expects the description to
  // stay in sync with their picks — a stale "young woman" description after
  // switching gender to Male is a bug. If the user hand-edited the field,
  // skip auto-regen so we don't clobber their text.
  useEffect(() => {
    if (voiceDescOverridden) return;
    if (voiceDescDebounceRef.current) clearTimeout(voiceDescDebounceRef.current);
    voiceDescDebounceRef.current = setTimeout(() => {
      handleGenerateDescription();
    }, 500);
    return () => { if (voiceDescDebounceRef.current) clearTimeout(voiceDescDebounceRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [avatarId, gender, language, accent]);

  // V1 mismatch detection — when the avatar arrives with a gender that
  // doesn't match the auto-generated description's pronouns/nouns, force a
  // regenerate once. We compare the gender pill against simple gendered
  // tokens in the description; a mismatch means the description was made
  // with stale picks and needs refreshing.
  useEffect(() => {
    if (!voiceDescription || voiceDescOverridden) return;
    const lower = voiceDescription.toLowerCase();
    const looksFemale = /\b(woman|girl|female|she|her|lady)\b/.test(lower);
    const looksMale = /\b(man|guy|male|he|his|gentleman|dude|boy)\b/.test(lower);
    const mismatch =
      (gender === "male" && looksFemale) ||
      (gender === "female" && looksMale);
    if (mismatch) {
      handleGenerateDescription();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceDescription, gender]);

  // Sync the gender chip when the avatar query lands with a different value
  // (e.g. user landed via deep-link; avatarData arrives async).
  useEffect(() => {
    if (avatar?.gender && avatar.gender !== gender) {
      setGender(avatar.gender as string);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [avatar?.gender]);

  const handleGenerateVoicePreviews = async () => {
    if (!voiceDescription.trim()) return;
    setIsGeneratingVoicePreviews(true);
    setVoicePreviews([]);
    setSelectedPreviewIdx(null);
    setVoiceScreen("previews");
    try {
      const data = await avatarApi.aiGenerateVoicePreviews(avatarId, voiceDescription.trim(), testSpeech, gender, language, accent);
      setVoicePreviews(data.previews);
    } catch (err: any) {
      toast({ title: "Voice generation failed", description: err?.response?.data?.detail || "Try again", variant: "destructive" });
      setVoiceScreen("description");
    } finally {
      setIsGeneratingVoicePreviews(false);
    }
  };

  const handleLockVoice = async () => {
    if (selectedPreviewIdx === null) return;
    const preview = voicePreviews[selectedPreviewIdx];
    setIsApprovingVoice(true);
    try {
      await avatarApi.aiApproveVoice(avatarId, preview.preview_id);
      onLockVoice(testSpeech.trim());
      toast({ title: "Voice locked!", description: "Your avatar's voice is ready." });
    } catch (err: any) {
      toast({ title: "Voice training failed", description: err?.response?.data?.detail || "Try again", variant: "destructive" });
    } finally {
      setIsApprovingVoice(false);
    }
  };

  const playPreview = (idx: number) => {
    audioRefs.current.forEach((a, i) => {
      if (a && i !== idx) { a.pause(); a.currentTime = 0; }
    });
    if (playingIdx === idx) {
      audioRefs.current[idx]?.pause();
      setPlayingIdx(null);
    } else {
      audioRefs.current[idx]?.play();
      setPlayingIdx(idx);
    }
  };

  return (
    <div className="flex gap-6">
      {/* Identity panel — left column */}
      {avatar && (
        <div className="hidden lg:block w-[320px] shrink-0">
          <AvatarIdentityPanel avatar={avatar} className="sticky top-4" />
        </div>
      )}
      <div className="flex-1 min-w-0">
        {/* Voice description screen */}
        {voiceScreen === "description" && (
          <div className="rounded-xl border border-border bg-surface p-6 space-y-5">
            <div className="space-y-1">
              <h2 className="text-base font-semibold text-text flex items-center gap-2">
                <Mic className="h-4 w-4 text-accent" /> Voice profile for {avatarName || "your avatar"}
              </h2>
              <p className="text-xs text-text-muted">Describe how {avatarName || "they"} should sound.</p>
            </div>

            <ShimmerField isLoading={isGeneratingVoiceDesc} className="min-h-[120px]">
              <div className="space-y-2">
                <textarea
                  value={voiceDescription}
                  onChange={(e) => { setVoiceDescription(e.target.value); setVoiceDescOverridden(true); }}
                  placeholder="Warm, energetic, youthful voice with..."
                  className="w-full rounded-md border border-border bg-bg p-3 text-sm text-text resize-none focus:border-accent focus:outline-hidden"
                  rows={4}
                />
                <div className="flex justify-end">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => { setVoiceDescOverridden(false); handleGenerateDescription(); }}
                    disabled={isGeneratingVoiceDesc}
                    title="Regenerate the description so it reflects the gender, language, and accent picked below"
                    data-testid="regenerate-voice-description"
                  >
                    <RefreshCw className={cn("mr-1 h-3 w-3", isGeneratingVoiceDesc && "animate-spin")} /> Regenerate description
                  </Button>
                </div>
              </div>
            </ShimmerField>

            {/* Clip-mic vs phone-mic toggle. Default OFF = phone mic. The
              backend appends a mic-style suffix to the voice description
              AND switches the TTS post-process EQ profile to match. */}
            <ClipMicToggle
              avatarId={avatarId}
              initialEnabled={!!(avatar as any)?.clip_mic_enabled}
            />

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-[10px] text-text-muted uppercase tracking-wide">Gender</label>
                <div className="flex gap-1.5">
                  {GENDER_OPTIONS.map((g) => (
                    <button
                      key={g.value}
                      onClick={() => setGender(g.value)}
                      className={cn(
                        "flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium transition",
                        gender === g.value
                          ? "text-white"
                          : "bg-surface border border-border text-text-muted hover:border-accent/40",
                      )}
                      style={gender === g.value ? { backgroundColor: "var(--accent-active)" } : undefined}
                    >
                      <span className="text-xs">{g.icon}</span> {g.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="mb-1 block text-[10px] text-text-muted uppercase tracking-wide">Language</label>
                <select
                  value={language}
                  onChange={(e) => { setLanguage(e.target.value); setAccent(LANGUAGES_WITH_ACCENTS[e.target.value]?.accents[0]?.value || ""); }}
                  className="w-full rounded-md border border-border bg-bg px-2 py-1.5 text-xs text-text"
                >
                  {LANGUAGE_OPTIONS.map((l) => (
                    <option key={l} value={l}>{LANGUAGES_WITH_ACCENTS[l].label}</option>
                  ))}
                </select>
              </div>
            </div>

            {LANGUAGES_WITH_ACCENTS[language]?.accents.length > 1 && (
              <div>
                <label className="mb-1 block text-[10px] text-text-muted uppercase tracking-wide">Accent</label>
                <div className="flex flex-wrap gap-1.5">
                  {LANGUAGES_WITH_ACCENTS[language].accents.map((a) => (
                    <button
                      key={a.value}
                      onClick={() => setAccent(a.value)}
                      className={cn(
                        "rounded-full px-3 py-1 text-xs font-medium transition",
                        accent === a.value
                          ? "text-white"
                          : "bg-surface border border-border text-text-muted hover:border-accent/40",
                      )}
                      style={accent === a.value ? { backgroundColor: "var(--accent-active)" } : undefined}
                    >
                      {a.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div>
              <label className="mb-1 block text-[10px] text-text-muted uppercase tracking-wide">Test speech</label>
              <textarea
                value={testSpeech}
                onChange={(e) => setTestSpeech(e.target.value)}
                placeholder={`Hi everyone! I'm ${avatarName}...`}
                className="w-full rounded-md border border-border bg-bg p-3 text-sm text-text resize-none focus:border-accent focus:outline-hidden"
                rows={3}
              />
            </div>

            <div className="flex gap-2 flex-wrap">
              <Button onClick={handleGenerateVoicePreviews} disabled={isGeneratingVoicePreviews || !voiceDescription.trim()} className="flex-1">
                {isGeneratingVoicePreviews ? (
                  <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Generating voices...</>
                ) : (
                  <><Mic className="mr-2 h-4 w-4" /> Generate voice options</>
                )}
              </Button>
              <Button variant="outline" onClick={() => setShowVoiceBrowser(true)}>
                Browse voice library
              </Button>
            </div>

            <VoiceCorpusTab
              avatarId={avatarId}
              ensureAvatarId={async () => avatarId}
              compact
              onTrained={() => {
                toast({ title: "Voice trained!", description: "Your custom voice is locked in." });
                onLockVoice(testSpeech.trim());
              }}
            />

            <div className="mt-4">
              <LiveReferenceCard
                avatarId={avatarId}
                title="Live Voice"
                subtitle="Upload past live sessions to teach this avatar your selling style."
              />
            </div>

            {showVoiceBrowser && (
              <div className="mt-4 rounded-xl border border-border bg-bg p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-text">Voice Library</h3>
                  <button onClick={() => setShowVoiceBrowser(false)} className="text-text-muted hover:text-text"><X className="h-4 w-4" /></button>
                </div>
                <VoiceBrowser avatarId={avatarId} onSelectVoice={(voiceId: string) => {
                  avatarApi.aiSelectVoice(avatarId, { voice_id: voiceId }).then(() => {
                    onLockVoice(testSpeech.trim());
                    toast({ title: "Voice selected and locked!" });
                  }).catch(() => toast({ title: "Failed to select voice", variant: "destructive" }));
                }} onClose={() => setShowVoiceBrowser(false)} />
              </div>
            )}
          </div>
        )}

        {/* Voice previews screen */}
        {voiceScreen === "previews" && (
          <div className="rounded-xl border border-border bg-surface p-6 space-y-5">
            <div className="space-y-1">
              <h2 className="text-base font-semibold text-text flex items-center gap-2">
                <Volume2 className="h-4 w-4 text-accent" /> Pick {avatarName || "your avatar"}'s voice
              </h2>
              <p className="text-xs text-text-muted">Listen to each option and select the best match.</p>
            </div>

            {/* Editable voice description with Regenerate — surfaced here so the
              user can refine the prompt and re-roll the 4 options without
              clicking Back to the description screen. Same Regenerate handler
              as the description screen so the gender/language/accent filters
              and current testSpeech are reused. */}
            <div className="flex items-start gap-3 rounded-lg border border-border bg-bg/50 p-3">
              <div className="flex-1 min-w-0 space-y-1">
                <p className="text-[10px] text-text-muted uppercase tracking-wide">
                  Voice description
                </p>
                <textarea
                  value={voiceDescription}
                  onChange={(e) => { setVoiceDescription(e.target.value); setVoiceDescOverridden(true); }}
                  placeholder="Warm, energetic, youthful voice with..."
                  rows={3}
                  disabled={isGeneratingVoicePreviews}
                  className="w-full resize-y rounded-md border border-border/50 bg-background/60 px-2 py-1.5 text-xs text-text placeholder:text-text-muted leading-relaxed focus:outline-none focus:ring-1 focus:ring-accent/40 disabled:opacity-60"
                  data-testid="voice-description-edit"
                />
              </div>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0 self-start mt-5"
                onClick={handleGenerateVoicePreviews}
                disabled={isGeneratingVoicePreviews || !voiceDescription.trim()}
                title={
                  !voiceDescription.trim()
                    ? "Add a voice description first"
                    : isGeneratingVoicePreviews
                      ? "Generation in progress"
                      : "Regenerate the 4 voice options from this description"
                }
                data-testid="regenerate-voice-previews"
              >
                <RefreshCw className={cn("mr-1 h-3 w-3", isGeneratingVoicePreviews && "animate-spin")} /> Regenerate
              </Button>
            </div>

            {isGeneratingVoicePreviews ? (
              <div className="flex flex-col items-center py-8 gap-3">
                <Loader2 className="h-8 w-8 animate-spin text-accent" />
                <p className="text-text-muted text-sm">Generating 4 voice options...</p>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                {voicePreviews.map((preview, idx) => (
                  <div
                    key={preview.preview_id}
                    className={cn(
                      "rounded-xl border-2 p-4 flex flex-col gap-3 cursor-pointer transition-all",
                      selectedPreviewIdx === idx ? "ring-1 ring-accent/30" : "border-border hover:border-accent/40",
                    )}
                    style={selectedPreviewIdx === idx ? { borderColor: "var(--accent-active)", backgroundColor: "var(--accent-active-muted)" } : undefined}
                    onClick={() => setSelectedPreviewIdx(idx)}
                  >
                    <p className="text-sm font-medium text-text">Voice {idx + 1}</p>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={(e) => { e.stopPropagation(); playPreview(idx); }}
                        className="h-10 w-10 rounded-full bg-accent/20 flex items-center justify-center hover:bg-accent/30 transition"
                      >
                        {playingIdx === idx ? <Pause className="h-4 w-4 text-accent" /> : <Play className="h-4 w-4 text-accent ml-0.5" />}
                      </button>
                      <div className="flex-1 h-2 bg-border rounded-full overflow-hidden">
                        <div className={cn("h-full bg-accent rounded-full transition-all", playingIdx === idx ? "w-1/2 animate-pulse" : "w-0")} />
                      </div>
                      <audio
                        ref={(el) => { audioRefs.current[idx] = el; }}
                        src={preview.audio_url}
                        onEnded={() => setPlayingIdx(null)}
                      />
                    </div>
                    <p className="text-xs text-text-muted">
                      {selectedPreviewIdx === idx ? "Selected" : "Click to select"}
                    </p>
                  </div>
                ))}
              </div>
            )}

            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setVoiceScreen("description")}>
                <ArrowLeft className="mr-1.5 h-3.5 w-3.5" /> Back
              </Button>
              <Button
                onClick={handleLockVoice}
                disabled={selectedPreviewIdx === null || isApprovingVoice}
                className="flex-1"
              >
                {isApprovingVoice ? (
                  <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Training voice...</>
                ) : (
                  <><Lock className="mr-2 h-4 w-4" /> Lock this voice & continue</>
                )}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ═══ BodyShotsPhase — 3x2 grid, per-tile regenerate ═══ */

function BodyShotsPhase({
  avatarId,
  avatarName,
  lockedFaceUrl,
  avatar,
  onContinue,
  onBack,
}: {
  avatarId: string;
  avatarName: string;
  lockedFaceUrl: string | null;
  avatar?: Avatar;
  onContinue: () => void;
  onBack: () => void;
}) {
  const [angles, setAngles] = useState<Record<string, string>>({});
  const [setId, setSetId] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [regeneratingAngle, setRegeneratingAngle] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<Record<string, { expected: string; classified: string; match: boolean }>>({});
  // Wardrobe-aware description that the pipeline actually used to generate
  // the body shots. May differ from avatar.body_description when the user
  // edited the face after the description was first generated. Surfaces in
  // the left identity panel so the user sees what was really fed to the
  // image model (e.g. "suit + no glasses" instead of stale "hoodie" text).
  const [descriptionUsed, setDescriptionUsed] = useState<string | null>(null);

  // Editable body description — hydrated from the avatar record. The user can
  // refine it here and click Regenerate to kick off a fresh body-shot pass
  // using the updated text. Edits persist back to the avatar record before
  // the job starts (the orchestrator pipeline reads body_description from
  // the DB on job start), and the Identity panel mirrors the live value.
  const [bodyDescription, setBodyDescription] = useState<string>(
    avatar?.body_description || ""
  );
  // Re-hydrate when the avatar query lands or the user changes which avatar
  // they're editing. We only overwrite local edits if the user hasn't typed
  // anything yet (i.e. local state matches the previously-seen server value).
  const lastServerBodyDescRef = useRef<string | undefined>(avatar?.body_description);
  useEffect(() => {
    const incoming = avatar?.body_description ?? "";
    if (lastServerBodyDescRef.current !== incoming) {
      // Only auto-update local state if the user hasn't edited it yet.
      if (bodyDescription === (lastServerBodyDescRef.current ?? "")) {
        setBodyDescription(incoming);
      }
      lastServerBodyDescRef.current = incoming;
    }
  }, [avatar?.body_description]);

  // The pipeline takes ~60s and now runs as a background job server-side to
  // avoid Cloudflare's ~100s edge timeout. We POST once to kick it off, then
  // poll the GET endpoint every 2s until status is 'completed' or 'failed'.
  // Tracks the cancel function for the active job so Regenerate can abort
  // any in-flight poll loop before kicking off a new one.
  const cancelActiveJobRef = useRef<(() => void) | null>(null);

  // Kick off (or re-kick-off) the body-shot pipeline. Returns a cancel fn
  // the caller can store to abort the polling loop on unmount/regenerate.
  const startBodyShotJob = useCallback((): (() => void) => {
    let cancelled = false;
    let pollHandle: ReturnType<typeof setTimeout> | null = null;

    setIsGenerating(true);
    setError(null);
    setAngles({});
    setValidation({});

    const poll = async (sid: string) => {
      if (cancelled) return;
      try {
        const data = await avatarApi.aiGetBodyShotSet(sid);
        if (cancelled) return;
        if (data.status === "completed") {
          if (data.angles) setAngles(data.angles);
          if (data.validation) setValidation(data.validation);
          if (data.description_used) setDescriptionUsed(data.description_used);
          setIsGenerating(false);
          return;
        }
        if (data.status === "failed") {
          setError(data.error || "Body shot generation failed");
          toast({ title: "Body shot generation failed", variant: "destructive" });
          setIsGenerating(false);
          return;
        }
        // status === 'running': poll again. 2s interval keeps the UI responsive
        // without hammering the server. The job typically finishes in ~60s, so
        // ~30 polls per generation is fine.
        pollHandle = setTimeout(() => poll(sid), 2000);
      } catch (err: any) {
        if (cancelled) return;
        setError(err?.response?.data?.detail || "Body shot generation failed");
        toast({ title: "Body shot generation failed", variant: "destructive" });
        setIsGenerating(false);
      }
    };

    avatarApi.aiGenerateBodyShots(avatarId).then((data) => {
      if (cancelled) return;
      setSetId(data.set_id);
      poll(data.set_id);
    }).catch((err: any) => {
      if (cancelled) return;
      setError(err?.response?.data?.detail || "Body shot generation failed");
      toast({ title: "Body shot generation failed", variant: "destructive" });
      setIsGenerating(false);
    });

    return () => {
      cancelled = true;
      if (pollHandle) clearTimeout(pollHandle);
    };
  }, [avatarId]);

  // Hydrate-or-generate on mount.
  //
  // The previous implementation unconditionally kicked off a new
  // generate-body-shots job every time this phase mounted. That meant
  // navigating away and coming back — or just letting the parent rerender
  // — wiped the existing shots and started over, even if a fully-completed
  // set already lived in the database.
  //
  // Now we ask the server for the latest BodyShotSet first. If one exists
  // we hydrate the UI from it (and resume polling if it's still running);
  // we only call aiGenerateBodyShots when the avatar has never run this
  // pipeline before. Explicit Regenerate is still wired through
  // handleRegenerateAll → startBodyShotJob.
  // const initialMountRef = useRef(false);
  useEffect(() => {
    // if (initialMountRef.current) return;
    // initialMountRef.current = true;

    let cancelled = false;
    let pollHandle: ReturnType<typeof setTimeout> | null = null;

    const resumePoll = (sid: string) => {
      const tick = async () => {
        if (cancelled) return;
        try {
          const data = await avatarApi.aiGetBodyShotSet(sid);
          if (cancelled) return;
          if (data.status === "completed") {
            if (data.angles) setAngles(data.angles);
            if (data.validation) setValidation(data.validation);
            if (data.description_used) setDescriptionUsed(data.description_used);
            setIsGenerating(false);
            return;
          }
          if (data.status === "failed") {
            setError(data.error || "Body shot generation failed");
            setIsGenerating(false);
            return;
          }
          pollHandle = setTimeout(tick, 2000);
        } catch {
          if (cancelled) return;
          // Network blip — keep retrying.
          pollHandle = setTimeout(tick, 4000);
        }
      };
      tick();
    };

    (async () => {
      try {
        const { set } = await avatarApi.aiGetLatestBodyShotSet(avatarId);
        if (cancelled) return;
        if (set) {
          // Hydrate from the existing set.
          setSetId(set.set_id);
          if (set.angles) setAngles(set.angles);
          if (set.validation) setValidation(set.validation);
          if (set.description_used) setDescriptionUsed(set.description_used);
          if (set.status === "running") {
            setIsGenerating(true);
            resumePoll(set.set_id);
          } else if (set.status === "failed") {
            setError(set.error || "Body shot generation failed");
            setIsGenerating(false);
          } else {
            // completed
            setIsGenerating(false);
          }
          return;
        }
        // No prior set — actually kick off a fresh job.
        const cancelJob = startBodyShotJob();
        cancelActiveJobRef.current = cancelJob;
      } catch (err) {
        if (cancelled) return;
        // If the latest-set lookup fails, fall back to the old behaviour
        // so the user isn't stuck waiting forever.
        const cancelJob = startBodyShotJob();
        cancelActiveJobRef.current = cancelJob;
      }
    })();

    return () => {
      cancelled = true;
      if (pollHandle) clearTimeout(pollHandle);
      cancelActiveJobRef.current?.();
      cancelActiveJobRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [avatarId]);

  // Regenerate the entire set with the (possibly edited) body description.
  const handleRegenerateAll = async () => {
    if (!bodyDescription.trim() || isGenerating) return;
    // Persist the edited description so the orchestrator picks it up when it
    // reads avatar.body_description at the start of the new job. Failure here
    // is non-fatal — we still kick off generation, but warn so the user knows
    // the change may not survive a reload.
    try {
      await avatarApi.updateAvatar(avatarId, { body_description: bodyDescription.trim() });
      queryClient.invalidateQueries({ queryKey: ["avatar-identity", avatarId] });
    } catch (err) {
      console.warn("updateAvatar(body_description) failed; continuing with regenerate", err);
      toast({
        title: "Could not save the description before regenerating",
        description: "Generation will use the previously saved description.",
        variant: "destructive",
      });
    }

    // Cancel any in-flight job poll loop, then kick off a fresh one.
    cancelActiveJobRef.current?.();
    cancelActiveJobRef.current = startBodyShotJob();
  };

  const regenerateAngle = async (angle: string) => {
    if (!setId) return;
    setRegeneratingAngle(angle);
    try {
      const result = await avatarApi.aiRegenerateBodyShot(avatarId, setId, angle);
      setAngles((prev) => ({ ...prev, [result.angle]: result.url }));
      if (result.validation) {
        setValidation((prev) => ({ ...prev, [result.angle]: result.validation! }));
      } else {
        setValidation((prev) => {
          const next = { ...prev };
          delete next[result.angle];
          return next;
        });
      }
      toast({ title: `${ANGLE_LABELS[angle]} regenerated` });
    } catch {
      toast({ title: `Failed to regenerate ${ANGLE_LABELS[angle]}`, variant: "destructive" });
    } finally {
      setRegeneratingAngle(null);
    }
  };

  const ANGLE_LABELS: Record<string, string> = {
    front: "Front",
    three_quarter_left: "3/4 Left",
    three_quarter_right: "3/4 Right",
    profile_left: "Profile Left",
    profile_right: "Profile Right",
    back: "Back",
  };

  const ANGLE_ORDER = ["front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back"];

  return (
    <div className="flex gap-6">
      {/* Identity panel — left column. The body description override mirrors
          the local editable textarea so the panel reflects unsaved edits.
          Once a body-shot set has actually run, prefer the wardrobe-aware
          description_used returned by the orchestrator — that's what the
          image model actually saw, so it's what the user should see in the
          left panel (e.g. "wearing a charcoal suit, no glasses" instead of
          the original setup-time hoodie text). */}
      {avatar && (
        <div className="hidden lg:block w-[320px] shrink-0">
          <AvatarIdentityPanel
            avatar={avatar}
            className="sticky top-4"
            bodyDescriptionOverride={descriptionUsed ?? bodyDescription}
          />
        </div>
      )}
      <div className="flex-1 min-w-0 space-y-6">
        <div>
          <h3 className="text-lg font-semibold text-text mb-2">Body Shots</h3>
          <p className="text-sm text-text-muted">6 angle shots generated from a full-body reference. Approve all to continue.</p>
        </div>

        {/* Editable body description — same pattern as the Face step. The user
          can refine the prompt and hit Regenerate to kick off a fresh job
          using the updated text. Disabled while a job is in flight to avoid
          stacking concurrent pipelines. */}
        <div className="flex items-start gap-3 rounded-lg border border-border bg-bg/50 p-3">
          <div className="flex-1 min-w-0 space-y-1">
            <p className="text-[10px] text-text-muted uppercase tracking-wide">
              Body description for {avatarName || "your avatar"}
            </p>
            <textarea
              value={bodyDescription}
              onChange={(e) => setBodyDescription(e.target.value)}
              placeholder="Describe the avatar's full body — build, posture, clothing, accessories. The pipeline uses this to generate the 6 angle shots below."
              rows={3}
              disabled={isGenerating}
              className="w-full resize-y rounded-md border border-border/50 bg-background/60 px-2 py-1.5 text-xs text-text placeholder:text-text-muted leading-relaxed focus:outline-none focus:ring-1 focus:ring-accent/40 disabled:opacity-60"
              data-testid="body-description-edit"
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 self-start mt-5"
            onClick={handleRegenerateAll}
            disabled={isGenerating || !bodyDescription.trim()}
            title={
              !bodyDescription.trim()
                ? "Add a body description first"
                : isGenerating
                  ? "Generation in progress"
                  : "Regenerate all 6 angles from this description"
            }
            data-testid="regenerate-all-body-shots"
          >
            <RefreshCw className={cn("mr-1 h-3 w-3", isGenerating && "animate-spin")} /> Regenerate
          </Button>
        </div>

        {isGenerating ? (
          <div className="flex flex-col items-center justify-center py-12 space-y-3">
            <Loader2 className="h-8 w-8 animate-spin text-accent" />
            <span className="text-text-muted">Generating 6 body shots... this takes about 90 seconds</span>
            <Progress value={30} className="w-48" />
          </div>
        ) : error ? (
          <div className="text-center py-8">
            <p className="text-red-500 mb-4">{error}</p>
            <Button onClick={onBack} variant="outline">
              <ArrowLeft className="h-4 w-4 mr-2" /> Back
            </Button>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3">
              {ANGLE_ORDER.map((angle) => {
                const angleMismatch = validation[angle] && !validation[angle].match;
                return (
                  <div key={angle} className="space-y-1" style={{ minWidth: 280 }}>
                    <div className="relative group">
                      {angles[angle] ? (
                        <>
                          <img
                            src={angles[angle]}
                            alt={ANGLE_LABELS[angle]}
                            className={`w-full rounded-lg border ${angleMismatch ? "border-yellow-500 border-2" : "border-border"}`}
                            style={{ aspectRatio: "9/16", objectFit: "cover", minWidth: 280 }}
                            data-testid={`body-shot-${angle}`}
                          />
                          {angleMismatch && (
                            <div className="absolute bottom-12 left-2 right-2">
                              <div className="bg-yellow-500/90 text-black text-xs font-medium px-2 py-1.5 rounded-md flex items-center justify-between gap-1">
                                <span>Angle looks wrong</span>
                                <button
                                  onClick={() => regenerateAngle(angle)}
                                  className="underline font-semibold whitespace-nowrap"
                                  data-testid={`regen-warning-${angle}`}
                                >
                                  Regenerate
                                </button>
                              </div>
                            </div>
                          )}
                          {regeneratingAngle === angle ? (
                            <div className="absolute inset-0 bg-black/40 rounded-lg flex items-center justify-center">
                              <Loader2 className="h-6 w-6 animate-spin text-white" />
                            </div>
                          ) : (
                            <button
                              onClick={() => regenerateAngle(angle)}
                              className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity bg-black/60 hover:bg-black/80 text-white rounded-lg p-1.5"
                              title={`Regenerate ${ANGLE_LABELS[angle]}`}
                              data-testid={`regenerate-${angle}`}
                            >
                              <RefreshCw className="h-4 w-4" />
                            </button>
                          )}
                        </>
                      ) : (
                        <div className="w-full rounded-lg border border-border bg-surface flex items-center justify-center" style={{ aspectRatio: "9/16", minWidth: 280 }}>
                          <Loader2 className="h-6 w-6 animate-spin text-accent/50" />
                        </div>
                      )}
                    </div>
                    <p className="text-xs text-text-muted text-center">{ANGLE_LABELS[angle]}</p>
                  </div>
                );
              })}
            </div>

            <div className="flex gap-2">
              <Button onClick={onBack} variant="ghost">
                <ArrowLeft className="h-4 w-4 mr-2" /> Back
              </Button>
              <Button
                onClick={onContinue}
                disabled={Object.keys(angles).length < 1}
                className="ml-auto"
                size="lg"
                data-testid="approve-body-shots-btn"
              >
                Approve All & Continue
                <ArrowRight className="h-4 w-4 ml-2" />
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ═══ PreviewPhase sub-component ═══ */

function PreviewPhase({
  avatarId,
  avatarName,
  description,
  lockedFaceUrl,
  avatar,
  testScript,
  onBackToFace,
}: {
  avatarId: string;
  avatarName: string;
  description: string;
  lockedFaceUrl: string | null;
  avatar?: Avatar;
  testScript: string;
  onBackToFace: () => void;
}) {
  const navigate = useNavigate();
  const [fullscreenVideo, setFullscreenVideo] = useState<string | null>(null);
  const [isGeneratingPreview, setIsGeneratingPreview] = useState(false);

  const { data: avatarStatus } = useQuery({
    queryKey: ["avatar-status", avatarId],
    queryFn: () => avatarApi.status(avatarId),
    enabled: !!avatarId && isGeneratingPreview,
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (avatarStatus?.status === AvatarStatus.READY || avatarStatus?.status === AvatarStatus.APPROVED) {
      setIsGeneratingPreview(false);
    }
  }, [avatarStatus?.status]);

  const approveMutation = useMutation({
    mutationFn: () => avatarApi.approve(avatarId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["avatars"] });
      toast({ title: "Avatar approved!", description: "Ready for Cast creation.", variant: "success" });
      navigate("/my-avatar");
    },
    onError: () => toast({ title: "Approval failed", variant: "destructive" }),
  });

  const generatePreview = async () => {
    setIsGeneratingPreview(true);
    try {
      await avatarApi.aiGeneratePreview(avatarId, { test_script: testScript });
    } catch (err: any) {
      toast({ title: "Generation failed", description: err?.response?.data?.detail || "Try again", variant: "destructive" });
      setIsGeneratingPreview(false);
    }
  };

  useEffect(() => {
    if (!avatarStatus?.test_video_url && !isGeneratingPreview) {
      generatePreview();
    }
  }, []);

  return (
    <div className="flex gap-6">
      {/* Identity panel — left column */}
      {avatar && (
        <div className="hidden lg:block w-[320px] shrink-0">
          <AvatarIdentityPanel avatar={avatar} className="sticky top-4" />
        </div>
      )}
      <div className="flex-1 min-w-0 space-y-5">
        <h2 className="text-base font-semibold text-text">Preview your avatar</h2>

        <div className="text-sm text-text-muted">
          Generating preview using your selected voice and the introduction text from the previous step.
        </div>

        {!isGeneratingPreview && (!avatarStatus || (avatarStatus.status !== AvatarStatus.READY && avatarStatus.status !== AvatarStatus.APPROVED && avatarStatus.status !== AvatarStatus.FAILED)) && (
          <Button onClick={generatePreview} className="w-full">
            Generate Preview
          </Button>
        )}

        {isGeneratingPreview && (
          <div className="space-y-3 py-4">
            <div className="flex items-center gap-2 text-sm text-text">
              <Loader2 className="h-4 w-4 animate-spin text-accent" />
              <span>{avatarStatus?.progress_step || "Generating preview..."}</span>
            </div>
            <p className="text-xs text-text-muted">
              This usually takes 2-5 minutes.
            </p>
          </div>
        )}

        {(avatarStatus?.status === AvatarStatus.READY || avatarStatus?.status === AvatarStatus.APPROVED) && (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <CheckCircle className="h-4 w-4 text-green-500" />
              <span className="text-sm text-green-500">
                {avatarStatus.status === AvatarStatus.APPROVED ? "Avatar approved!" : "Preview ready!"}
              </span>
            </div>
            {avatarStatus.test_video_url && (
              <div className="mx-auto" style={{ maxWidth: 340 }}>
                <video
                  src={avatarStatus.test_video_url}
                  controls
                  playsInline
                  preload="auto"
                  className="w-full rounded-xl border border-border bg-black"
                  // onClick={() => avatarStatus?.test_video_url && setFullscreenVideo(avatarStatus.test_video_url)}
                  style={{ aspectRatio: "9/16" }}
                  data-testid="preview-video"
                />
              </div>
            )}

            <Button variant="outline" onClick={onBackToFace} data-testid="re-edit-generate-btn">
              <RotateCcw className="w-4 h-4 mr-2" />
              Re-edit & generate
            </Button>

            {avatarStatus.status === AvatarStatus.READY && (
              <Button
                onClick={() => approveMutation.mutate()}
                disabled={approveMutation.isPending}
                className="w-full"
                data-testid="approve-avatar-btn"
              >
                {approveMutation.isPending ? (
                  <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Approving...</>
                ) : (
                  <><CheckCircle className="mr-2 h-4 w-4" /> Approve avatar</>
                )}
              </Button>
            )}
          </div>
        )}

        {avatarStatus?.status === AvatarStatus.FAILED && (
          <div className="space-y-3 text-center py-4">
            <p className="text-sm text-red-500">{avatarStatus.progress_step || "Generation failed"}</p>
            <Button size="sm" onClick={generatePreview}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Retry
            </Button>
          </div>
        )}

        {fullscreenVideo && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80"
            onClick={() => setFullscreenVideo(null)}
          >
            <div className="relative max-w-sm w-full mx-4" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
              <video
                src={fullscreenVideo}
                controls autoPlay playsInline
                className="w-full rounded-xl"
                style={{ aspectRatio: "9/16" }}
              />
              <button
                onClick={() => setFullscreenVideo(null)}
                className="absolute top-2 right-2 rounded-full bg-black/50 p-1.5 text-white/70 hover:text-white transition"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ═══ Parent orchestrator ═══ */

const PHASE_ORDER: Phase[] = ["setup", "face", "voice", "body_shots", "preview"];

export function AIAvatarSetupPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { avatarId: routeAvatarId, step: routeStep } = useParams<{ avatarId?: string; step?: string }>();
  const resumeId = routeAvatarId || searchParams.get("resume");

  const [phase, setPhase] = useState<Phase>("setup");
  const [completedPhases, setCompletedPhases] = useState<Phase[]>([]);
  const [isResuming, setIsResuming] = useState(!!resumeId);
  const [avatarId, setAvatarId] = useState<string | null>(null);
  const [avatarName, setAvatarName] = useState("");
  const [lockedFaceUrl, setLockedFaceUrl] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [audienceDescription, setAudienceDescription] = useState("");
  const [bodyDescription, setBodyDescription] = useState("");
  const [testScript, setTestScript] = useState("");
  const didCreateRef = useRef(false);


  // Avatar query — used to populate AvatarIdentityPanel in Face/Voice/Shots/Preview
  const { data: avatarData } = useQuery({
    queryKey: ["avatar-identity", avatarId],
    queryFn: () => avatarApi.status(avatarId!),
    enabled: !!avatarId && phase !== "setup",
    refetchInterval: 10_000,
  });

  // Hydrate testScript from the server's locked_test_script (set by
  // /lock-test-script when the user completes the Voice step). Without this,
  // navigating back to the Preview step or refreshing the page would reset
  // testScript to "", and PreviewPhase would call /generate-preview with an
  // empty test_script, leaving the pipeline to read whatever stale value is
  // in avatar.test_script (often the AI-generated greeting). We only adopt
  // the server value when the local state is empty, so an in-progress edit
  // is never clobbered by the polling refresh.
  useEffect(() => {
    const serverScript = avatarData?.locked_test_script;
    if (serverScript && !testScript) {
      setTestScript(serverScript);
    }
    // testScript intentionally not in deps — we only want to hydrate when
    // server data lands, not re-run on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [avatarData?.locked_test_script]);

  // Setup rehydration data (loaded from server for back-nav)
  const [setupInitialData, setSetupInitialData] = useState<{
    target_audience?: { age_range?: string; interests?: string[]; description?: string };
    description?: string;
    gender?: string;
    body_description?: string;
    style_preset?: string;
    imperfections?: string[];
  } | undefined>(undefined);
  // SetupPhase only reads `initialData` once, at mount (it's a useState
  // initializer, not something a later prop update can re-apply) — so on a
  // return visit we must hold off mounting it until the fresh fetch below
  // has actually landed, otherwise it mounts with whatever stale snapshot
  // was left over from the first visit and the fetch result never reaches
  // it. Not needed on the very first-ever visit (nothing saved yet to be
  // stale about, and gating it there would only risk discarding the
  // couple of characters a fast typist could enter before avatarId exists).
  const hasEnteredSetupRef = useRef(false);
  const [setupDataReady, setSetupDataReady] = useState(true);

  // Create avatar on mount OR resume existing avatar
  useEffect(() => {
    if (avatarId) return;
    if (resumeId) {
      setAvatarId(resumeId);
      setIsResuming(true);
      avatarApi.status(resumeId).then((data) => {
        if (data.name) setAvatarName(data.name);
        if (data.appearance_prompt) setDescription(data.appearance_prompt);
        if (data.description) setDescription(data.description);
        if (data.body_description) setBodyDescription(data.body_description);

        // Store setup data for rehydration
        setSetupInitialData({
          target_audience: data.target_audience || undefined,
          description: data.description || data.appearance_prompt || undefined,
          gender: data.gender || undefined,
          body_description: data.body_description || undefined,
          style_preset: data.style_preset || undefined,
        });

        if (data.status === AvatarStatus.READY || data.status === AvatarStatus.APPROVED) {
          navigate(`/my-avatar/${resumeId}/edit`);
          return;
        }
        // Infer entity phase from server state
        let entityPhase: Phase = "setup";
        const completed: Phase[] = [];
        if (data.preview_video_url || data.test_video_url || data.test_video_key) {
          entityPhase = "preview";
          completed.push("setup", "face", "voice", "body_shots");
          if (data.face_image_url) setLockedFaceUrl(data.face_image_url);
        } else if (data.body_description && data.voice_id) {
          entityPhase = "body_shots";
          completed.push("setup", "face", "voice");
          if (data.face_image_url) setLockedFaceUrl(data.face_image_url);
        } else if (data.voice_id) {
          entityPhase = "body_shots";
          completed.push("setup", "face", "voice");
          if (data.face_image_url) setLockedFaceUrl(data.face_image_url);
        } else if (data.face_ref_key) {
          entityPhase = "voice";
          completed.push("setup", "face");
          if (data.face_image_url) setLockedFaceUrl(data.face_image_url);
        } else if (data.target_audience) {
          entityPhase = "face";
          completed.push("setup");
        }
        setCompletedPhases(completed);

        // Deep-link rules: URL step <= entity phase → honor, step > entity phase → redirect
        const requestedStep = routeStep as Phase | undefined;
        if (requestedStep && PHASE_ORDER.includes(requestedStep)) {
          const entityIdx = PHASE_ORDER.indexOf(entityPhase);
          const requestedIdx = PHASE_ORDER.indexOf(requestedStep);
          setPhase(requestedIdx <= entityIdx ? requestedStep : entityPhase);
        } else {
          setPhase(entityPhase);
        }
        setIsResuming(false);
      }).catch(() => {
        setIsResuming(false);
        toast({ title: "Failed to resume avatar", variant: "destructive" });
        navigate("/my-avatar");
      });
    } else {
      if (didCreateRef.current) return;   // ← add this guard
      didCreateRef.current = true;         // ← set synchronously, before the async call
      // Two-arg .then(onSuccess, onError) — NOT .then(onSuccess).catch(onError).
      // The chained-.catch() form also catches errors thrown INSIDE
      // onSuccess (setAvatarId/navigate), wrongly reporting a request that
      // actually succeeded as "Failed to create avatar" — the redirect
      // would already have happened by then, so the user saw a working
      // navigation plus a destructive-looking toast for no real failure.
      // The two-arg form only invokes onError for an actual rejection of
      // the createAIAvatar() call itself.
      avatarApi.createAIAvatar({ name: "AI Avatar" }).then(
        (data) => {
          setAvatarId(data.avatar_id);
          navigate(`/my-avatar/ai/${data.avatar_id}/setup`, { replace: true });
        },
        (err: any) => {
          didCreateRef.current = false;   // allow retry if creation actually failed
          // Surface the backend's actual reason (e.g. "You've used all N
          // avatar slots on your plan...") instead of a generic message —
          // this is a real, actionable error (plan limit, payment issue),
          // not a mystery failure.
          toast({
            title: err?.response?.status === 402 ? "Avatar limit reached" : "Failed to create avatar",
            description: err?.response?.data?.detail || undefined,
            variant: "destructive",
          });
        },
      );
    }
  }, []);

  // Load setup data whenever we (re)enter the setup phase — including
  // navigating back to it after visiting Face/Voice/etc. This used to be
  // guarded by `!setupInitialData`, which is truthy after the very first
  // fetch (even one made before the user had entered anything), so it
  // never ran again — meaning a return visit to Setup always remounted
  // SetupPhase with that stale first-fetch snapshot instead of whatever
  // had actually been saved since.
  useEffect(() => {
    if (phase !== "setup" || !avatarId) return;
    const isReturnVisit = hasEnteredSetupRef.current;
    hasEnteredSetupRef.current = true;
    if (isReturnVisit) setSetupDataReady(false);
    avatarApi.status(avatarId).then((data) => {
      setSetupInitialData({
        target_audience: data.target_audience || undefined,
        description: data.description || data.appearance_prompt || undefined,
        gender: data.gender || undefined,
        body_description: data.body_description || undefined,
        style_preset: data.style_preset || undefined,
        imperfections: data.imperfections || undefined,
      });
      if (data.name) setAvatarName(data.name);
    }).catch(() => { }).finally(() => setSetupDataReady(true));
  }, [phase, avatarId]);

  // Phase transition helper — saves wizard_step and pushes URL
  const setPhaseAndSave = useCallback((newPhase: Phase) => {
    setPhase(newPhase);
    if (avatarId) {
      avatarApi.updateAvatar(avatarId, { wizard_step: newPhase }).catch(() => { });
      navigate(`/my-avatar/ai/${avatarId}/${newPhase}`, { replace: false });
    }
  }, [avatarId, navigate]);

  // Phase transitions
  const handleSetupContinue = useCallback((desc: string, audDesc: string, gender: string, bodyDesc: string) => {
    setDescription(desc);
    setAudienceDescription(audDesc);
    setBodyDescription(bodyDesc);
    setCompletedPhases((prev) => [...prev.filter((p) => p !== "setup"), "setup"]);
    setPhaseAndSave("face");
  }, [setPhaseAndSave]);

  const handleContinueToVoice = useCallback((faceUrl: string) => {
    setLockedFaceUrl(faceUrl);
    setCompletedPhases((prev) => [...prev.filter((p) => p !== "face"), "face"]);
    setPhaseAndSave("voice");
  }, [setPhaseAndSave]);

  const handleLockVoice = useCallback((testSpeechText: string) => {
    setCompletedPhases((prev) => [...prev.filter((p) => p !== "voice"), "voice"]);
    if (testSpeechText) setTestScript(testSpeechText);
    if (avatarId && testSpeechText) {
      avatarApi.aiLockTestScript(avatarId, testSpeechText).catch(() => { });
    }
    setPhaseAndSave("body_shots");
  }, [avatarId, setPhaseAndSave]);

  const handleBodyShotsContinue = useCallback(() => {
    setCompletedPhases((prev) => [...prev.filter((p) => p !== "body_shots"), "body_shots"]);
    setPhaseAndSave("preview");
  }, [setPhaseAndSave]);

  const handleBackToFaceFromVoice = useCallback(() => {
    setPhaseAndSave("face");
  }, [setPhaseAndSave]);

  const handleBackToFaceFromPreview = useCallback(() => {
    setPhaseAndSave("face");
  }, [setPhaseAndSave]);

  return (
    <div className={cn("mx-auto space-y-6 p-4 pb-20", phase === "setup" ? "max-w-2xl" : "max-w-5xl")}>
      {isResuming ? (
        <div className="flex flex-col items-center justify-center py-32 space-y-4">
          <Loader2 className="h-8 w-8 animate-spin text-accent" />
          <p className="text-sm text-text-muted">Resuming your avatar...</p>
        </div>
      ) : (<Fragment key={phase}>
        {/* F3 / V3: Back + Forward navigation row. Forward is enabled when a
          downstream step has previously been completed (i.e. the user
          progressed past `phase` and came back) so they can re-enter that
          step without losing state. */}
        <div className="flex items-center justify-between">
          <button
            onClick={() => {
              const idx = PHASE_ORDER.indexOf(phase);
              if (idx <= 0) navigate("/my-avatar");
              else setPhaseAndSave(PHASE_ORDER[idx - 1]);
            }}
            className="flex items-center gap-1 text-xs text-text-muted hover:text-text transition"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {phase === "setup" ? "Back to My Avatar" : "Back"}
          </button>
          {(() => {
            const idx = PHASE_ORDER.indexOf(phase);
            const next = idx >= 0 && idx < PHASE_ORDER.length - 1 ? PHASE_ORDER[idx + 1] : null;
            const canGoForward = !!next && completedPhases.includes(phase as Phase);
            return (
              <button
                onClick={() => { if (canGoForward && next) setPhaseAndSave(next); }}
                disabled={!canGoForward}
                className={cn(
                  "flex items-center gap-1 text-xs transition",
                  canGoForward ? "text-text-muted hover:text-text" : "text-text-muted/30 cursor-not-allowed",
                )}
                title={canGoForward ? `Forward to ${next}` : "Complete this step first"}
                data-testid="wizard-forward"
              >
                Forward
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            );
          })()}
        </div>

        <h1 className="text-xl font-bold text-text">Create your AI avatar</h1>

        {/* Phase indicator */}
        <div className="flex items-center gap-2 flex-wrap">
          {PHASE_STEPS.map((step, i) => {
            const isActive = phase === step.key;
            const isComplete = completedPhases.includes(step.key as Phase);
            return (
              <div key={step.key} className="flex items-center gap-2">
                {i > 0 && <div className={cn("h-px w-8", isComplete || isActive ? "bg-accent" : "bg-border")} />}
                <button
                  onClick={() => isComplete ? setPhaseAndSave(step.key as Phase) : undefined}
                  className={cn(
                    "flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-all",
                    isActive ? "text-white" : isComplete ? "text-accent cursor-pointer" : "bg-surface text-text-muted",
                  )}
                  style={isActive ? { backgroundColor: "var(--accent-active)" } : isComplete ? { backgroundColor: "var(--accent-active-muted)" } : undefined}
                >
                  {isComplete ? <CheckCircle className="h-3 w-3" /> : <span>{step.num}</span>}
                  {step.label}
                </button>
              </div>
            );
          })}
        </div>

        {/* Phase rendering — single expression to prevent React #310 hook mismatch */}
        {phase === "setup" && !setupDataReady ? (
          <div className="flex justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-accent" />
          </div>
        ) : phase === "setup" ? (
          <SetupPhase
            key="setup"
            avatarId={avatarId}
            avatarName={avatarName}
            setAvatarName={setAvatarName}
            onContinue={handleSetupContinue}
            initialData={setupInitialData}
          />
        ) : phase === "face" ? (
          <FacePhase
            key="face"
            avatarId={avatarId}
            avatarName={avatarName}
            description={description}
            setDescription={setDescription}
            avatar={avatarData}
            onContinueToVoice={handleContinueToVoice}
          />
        ) : phase === "voice" && avatarId ? (
          <VoicePhase
            key="voice"
            avatarId={avatarId}
            avatarName={avatarName}
            description={description}
            lockedFaceUrl={lockedFaceUrl}
            avatar={avatarData}
            onLockVoice={handleLockVoice}
            onBackToFace={handleBackToFaceFromVoice}
          />
        ) : phase === "body_shots" && avatarId ? (
          <BodyShotsPhase
            key="body_shots"
            avatarId={avatarId}
            avatarName={avatarName}
            lockedFaceUrl={lockedFaceUrl}
            avatar={avatarData}
            onContinue={handleBodyShotsContinue}
            onBack={() => setPhaseAndSave("voice")}
          />
        ) : phase === "preview" && avatarId ? (
          <PreviewPhase
            key="preview"
            avatarId={avatarId}
            avatarName={avatarName}
            description={description}
            lockedFaceUrl={lockedFaceUrl}
            avatar={avatarData}
            testScript={testScript}
            onBackToFace={handleBackToFaceFromPreview}
          />
        ) : null}
      </Fragment>)}
    </div>
  );
}
