# Visual Engineering - Art Direction: ShinkaEvolve UI

> **Note**: This is an *inspired-by* design system for ShinkaEvolve, drawing from Sakana AI's fish/evolution themes and David Ha's illustration style, but adapted for our product's needs.

## 1. Visual Philosophy: Evolutionary Cybernetics + Ghibli Warmth

The ShinkaEvolve aesthetic merges two worlds:
- **Ghibli Warmth**: Approachable, friendly, whimsical characters with big expressive eyes
- **Evolutionary Cybernetics**: Mechanical details, industrial pipes, scientific precision

This creates **"Evolved Cyber-Fish"** - creatures that are both organic and technological, warm yet sophisticated.

### Core Principles

1. **The Defiant Red Fish**: In a school swimming one direction, one red fish swims the other way. This represents innovation, contrarian thinking, and breakthrough moments. Red is the color of *agency*.

2. **Warm Technical**: The interface should feel like a research lab run by friendly robot fish - precise and scientific, but never cold or sterile.

3. **Evolved, Not Designed**: Visual elements should feel like they *emerged* through evolution - organic curves, biological patterns, swarm behaviors.

4. **Function in Service of Delight**: Every UI element works first, delights second. Whimsy is earned through clarity.

### The Character Spectrum

Our fish characters exist on a spectrum from simple to complex:

| Context | Complexity | Example |
|:--------|:-----------|:--------|
| **UI Icons** | Simple silhouette | Single-color fish shape, 16-24px |
| **Empty States** | Stylized illustration | Flat-color fish with goggles, ~128px |
| **Headers/Modals** | Detailed illustration | Full Ghibli-style scene with Mt. Fuji |
| **Hero/Marketing** | Complex mechanical | Steampunk cyber-fish with pipes, gears, mechanical legs |

---

## 2. Color System: Ocean, Defiance, and Warmth

The palette tells the story of the Red Fish swimming against the school.

### Primary Palette

| Role | Name | Hex | CSS Variable | Meaning |
|:-----|:-----|:----|:-------------|:--------|
| **Brand Primary** | Defiant Red | `#EF4444` | `--brand-red` | The Red Fish. Primary actions, breakthroughs, agency. |
| **Brand Secondary** | Ocean Depth | `#0F172A` | `--brand-depth` | The deep ocean. Headers, strong text, authority. |
| **Nature/Success** | Biolum Teal | `#2DD4BF` | `--accent-teal` | The school, collective intelligence, success states. |
| **Energy/Warmth** | Koi Orange | `#FF6B35` | `--accent-orange` | Celebration, energy, warmth, secondary highlights. |
| **Mutation** | Amber Gold | `#F59E0B` | `--warning` | Evolution in progress, warnings, transformations. |

### Functional Palette

| Role | Name | Hex | CSS Variable | Usage |
|:-----|:-----|:----|:-------------|:------|
| **Surface** | Lab White | `#FFFFFF` | `--surface` | Card backgrounds |
| **Background** | Mist Gray | `#F8FAFC` | `--background` | Page canvas |
| **Text Primary** | Sumi Ink | `#1E293B` | `--text-primary` | Body text |
| **Text Secondary** | Stone | `#64748B` | `--text-secondary` | Captions, metadata |
| **Border** | Cloud | `rgba(148,163,184,0.2)` | `--border` | Subtle separations |
| **Error** | Vermilion | `#DC2626` | `--error` | Error states |
| **Success** | Jade | `#10B981` | `--success` | Success states |

### Color Usage Philosophy

- **Defiant Red** is precious - use sparingly. If everything is red, nothing is defiant.
- **Teal** represents the collective, nature, and successful evolution.
- **Orange** brings warmth and energy - use for celebration and highlights.
- **The triad (Red + Teal + Orange)** should appear together in key illustrations.

### Water vs. Fish Color Contrast
In wave/ocean patterns, maintain clear color separation for beautiful contrast:
- **Water/Waves**: Always blue and teal tones. Never orange or red water.
- **Fish**: Red, coral, and orange. This creates striking contrast against blue waves.
- **The Metaphor**: The warm-colored fish (agency, life, defiance) stand out against the cool ocean (possibility space, collective). This contrast is essential to the visual story.

---

## 3. The Evolved Cyber-Fish Characters

Based on David Ha's illustration style for Sakana AI's blog.

### Character Types

| Character | Colors | Personality | Visual Details | Usage |
|:----------|:-------|:------------|:---------------|:------|
| **Teal Explorer** | `#2DD4BF` body, orange accents | Curious, helpful, guides users | Aviator goggles, small helmet, wing-like fins | Primary mascot, onboarding |
| **Orange Adventurer** | `#FF6B35` body, teal accents | Energetic, celebratory | Goggles pushed up, excited expression | Success states, achievements |
| **Red Defiant** | `#EF4444` body | Bold, innovative, breaks the mold | Swimming opposite direction, determined look | Breakthrough moments, CTAs |
| **Mecha-Fish** | Orange/teal with mechanical parts | Complex, evolved, powerful | Pipes, gears, mechanical legs, industrial details, transparent cockpit | Hero images, detailed illustrations |
| **Robot Buddy** | White/gray metallic | Reliable, technical, helpful | Round body, single eye, small arms | Error states, system messages |

### Character Traits (All Types)

- **Big expressive eyes** - Ghibli-style, convey emotion
- **Soft rounded bodies** - Approachable, not aggressive
- **Wing-like fins** - Suggest flight/freedom
- **Mechanical details** (for complex versions):
  - Pipes and tubes
  - Transparent sections showing inner workings
  - Industrial bolts and panels
  - Small mechanical legs or appendages
  - Antenna or periscopes
- **Optional accessories**: Aviator goggles, helmets, scarves, tool belts

### Scene Settings (for illustrations)

- Japanese cityscapes with Mt. Fuji backdrop
- Warm sky gradients (sunrise/sunset)
- Urban streets with signage
- Underwater/swimming through data streams
- Research lab environments

### The Red Fish: A Deep Dive

The **Defiant Red Fish** is the soul of ShinkaEvolve's visual identity. Unlike the teal school that swims together, the Red Fish ventures off on their own - not out of rebellion, but out of **joyful innovation**.

#### Personality
- **Happy and excited** - NOT grumpy or angry
- **Adventurous** - Eager to explore new directions
- **Confident** - Knows their own path
- **Fast mover** - First to try new things

#### Visual Expression
- **Expression**: Enthusiastic grin, bright eyes, joyful determination
- **Direction**: Swimming opposite to the school, or leading the charge forward
- **Energy**: Slightly more dynamic movement than teal fish

#### Accessories
| Mode | Accessory | Vibe |
|:-----|:----------|:-----|
| **Cool Mode** | Aviator sunglasses | Extra confident, "I've got this" energy |
| **Friendly Mode** | No accessories | Approachable, inviting others to follow |

#### Usage in UI
- **Primary CTAs** - The red button is the Red Fish's action
- **Breakthrough moments** - When evolution finds something special
- **Success celebrations** - Leading the victory lap
- **Compute slider** - Appears in fish schools at ~1:4 ratio (1 red per 4 teal)

#### The Metaphor
In a school of fish swimming one direction, the Red Fish swims the other way. But they're not a troublemaker - they're the **innovator who discovered a better path**. The school will eventually follow. This is the story of evolutionary breakthroughs: one mutation, one defiant choice, leads to something better.

---

## 4. UI Components

### Buttons

```css
/* The Defiant Button - Primary Action */
.btn-primary {
  background: var(--brand-red);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 10px 24px;
  font-weight: 600;
  transition: all 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
  box-shadow: 0 4px 6px -1px rgba(239, 68, 68, 0.2);
}
.btn-primary:hover {
  transform: translateY(-2px) scale(1.02);
  box-shadow: 0 10px 15px -3px rgba(239, 68, 68, 0.3);
}

/* The School Button - Secondary Action */
.btn-secondary {
  background: var(--surface);
  color: var(--brand-depth);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 24px;
  font-weight: 500;
}
.btn-secondary:hover {
  border-color: var(--accent-teal);
  background: rgba(45, 212, 191, 0.05);
}

/* The Nature Button - Success/Positive */
.btn-nature {
  background: var(--accent-teal);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 10px 24px;
  font-weight: 500;
}
```

### Cards

```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  padding: 20px;
  transition: all 0.2s ease;
}
.card:hover {
  border-color: var(--accent-teal);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}

/* Card with Defiant accent (for active/selected states) */
.card-active {
  border-color: var(--brand-red);
  box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.1);
}
```

### Input Fields

```css
.input {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 14px;
  transition: all 0.15s ease;
}
.input:focus {
  border-color: var(--accent-teal);
  box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.2);
  outline: none;
}
.input:invalid:not(:placeholder-shown) {
  border-color: var(--brand-red);
}
```

---

## 5. Typography

### Font Stack

| Role | Font | Weight | Size | Usage |
|:-----|:-----|:-------|:-----|:------|
| **UI Interface** | Inter | 400-600 | 12-16px | Navigation, buttons, labels |
| **Headings** | Inter | 600-700 | 16-32px | Section headers, titles |
| **Scientific Content** | Source Serif 4 | 400-700 | 16-20px | Generated papers, reports |
| **Code/Data** | JetBrains Mono | 400 | 13-14px | Code blocks, model weights |

### Type Scale

```
Display:   32px / 2.0rem  - Landing headers
H1:        28px / 1.75rem - Page titles
H2:        24px / 1.5rem  - Section headers
H3:        20px / 1.25rem - Card titles
Body:      16px / 1.0rem  - Standard text
Body-sm:   14px / 0.875rem - Dense UI text
Caption:   12px / 0.75rem - Labels, metadata
Code:      14px / 0.875rem - Monospace content
```

---

## 6. Iconography

### UI Icons
- **Library**: Lucide or Heroicons (stroke-based)
- **Stroke width**: 1.5-2px
- **Sizes**: 16px (inline), 20px (buttons), 24px (features)
- **Color**: Inherit from text or use functional colors

### Fish Icons (Custom)
Simple silhouette fish for inline use:
- Single color, no detail
- Conveys "fish" at small sizes
- Can be animated (swimming motion)

---

## 7. Mascot Usage Guidelines

### Appropriate Uses

| Context | Character | Size | Animation |
|:--------|:----------|:-----|:----------|
| **Empty states** | Teal Explorer (curious) | 128-200px | Gentle float |
| **Loading states** | School of fish | 64-128px | Swimming/converging |
| **Success celebration** | Orange Adventurer | 128px | Happy bounce |
| **Error states** | Robot Buddy (confused) | 128px | Slight wobble |
| **Onboarding** | Teal Explorer + Orange Adventurer | 200px+ | Guided tour |
| **Modal headers** | Stylized fish scene | Full width | Static or parallax |
| **404/Not Found** | Red Defiant (lost) | 200px | Swimming in circles |

### Never

- As clickable buttons (confusing affordance)
- Overlapping functional UI elements
- At sizes that dominate over content
- Without reduced-motion alternatives
- **Use emojis** - Emojis are lazy shortcuts. Use custom illustrations, icons, or proper typography instead. Our visual language is distinct and crafted, not borrowed from platform defaults.

---

## 8. Asset Generation

### For Simple UI Assets (EvoSDXL-JP style)

```
[subject description], ukiyoe-inspired style, clean vector lines,
tech-biological fusion, Japanese aesthetic, soft cel shading,
isolated on solid bright green background (#00FF00),
transparent-ready, simple clean design
```

### For Detailed Illustrations (Ghibli-Mecha style)

```
[subject description], studio ghibli meets steampunk,
mechanical fish robot with pipes and gears, warm colors,
teal and orange palette, Japanese cityscape with Mt Fuji,
detailed illustration, soft lighting, whimsical but technical
```

### For Character Portraits

```
cute robot fish character, big expressive eyes, aviator goggles,
[teal/orange/red] colored body, mechanical details, friendly expression,
studio ghibli style, soft cel shading, white background,
character design sheet style
```

### For Watercolor Background Effects

Watercolor ukiyo-e wave patterns work beautifully for subtle card backgrounds and hover states. The soft, organic texture adds warmth and depth without overwhelming UI elements. Best used at 10-20% opacity.

```
watercolor wave pattern, ukiyoe inspired, soft flowing curves,
teal and orange gradient with coral accents, organic texture,
Japanese aesthetic, seamless tile, transparent-ready
```

**Usage**: Card hover states, modal backgrounds, decorative borders. The watercolor effect embodies "evolved, not designed" - organic and warm rather than rigid and cold.

---

## 9. Layout Principles

### Spacing Scale
`4, 8, 12, 16, 24, 32, 48, 64px`

### Grid
- Max content width: 1200px
- Gutter: 24px
- Card grid: 2-3 columns desktop, single column mobile

### The "Schooling" Layout
For model galleries and evolutionary trees, use fluid/masonry layouts that suggest organic clustering rather than rigid grids.

### Hierarchy
1. **Primary action**: Defiant Red button
2. **Secondary action**: Ghost/outline button with teal hover
3. **Tertiary**: Text link in teal

---

## 10. Motion

### Principles
- **Fluid**: Motion should feel like swimming through water
- **Emergent**: Elements should feel like they're evolving into place
- **Purposeful**: Every animation communicates something

### Timing
- **Micro** (hover, focus): 150ms
- **Standard** (modals, dropdowns): 250ms
- **Emphasis** (page transitions): 400ms
- **Evolution** (complex visualizations): 800ms

### Easing
```css
/* Fluid ease */
transition: all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);

/* Bouncy (for success/celebration) */
transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
```

### Special Animations
- **Converging Swarm**: For loading states - particles moving randomly then aligning
- **Fish Wiggle**: Subtle body squash for swimming effect
- **Gentle Pulse**: Soft opacity pulse for idle states

---

## 11. Accessibility

- **Color contrast**: Minimum 4.5:1 for text (Defiant Red on white: 4.5:1 ✓)
- **Focus rings**: Visible, use `--accent-teal` with 3px spread
- **Touch targets**: Minimum 44x44px
- **Reduced motion**: Respect `prefers-reduced-motion` for all animations
- **Color + icon**: Never rely on color alone - always pair with icons/labels

---

## 12. The Story We Tell

Every interface tells the story of evolution:

1. **The Ocean** (background) - The vast possibility space
2. **The School** (data, models) - Collective intelligence, options
3. **The Red Fish** (user action) - Defiant choice, breakthrough
4. **Evolution** (results) - Emergence of something new

When a user clicks the red button, they're choosing to swim against the current. When the system succeeds, the school converges. When something evolves, it transforms from amber to teal.

This narrative isn't just decoration - it's the mental model that makes complex AI evolution intuitive.

---

## 13. Modal Banner Generation (Nano Banana Workflow)

### The Thin Banner Problem

Nano Banana outputs images at fixed dimensions (~1408x768). For thin modal banners (~8:1 ratio), you cannot prompt for specific dimensions. Instead:

**Solution: White Padding + Trim**
1. Prompt for content in a "thin horizontal strip" with white space above/below
2. Auto-trim with ImageMagick: `magick input.png -fuzz 5% -trim +repage output.png`
3. Result: banner-ready aspect ratio

### Effective Thin Banner Prompt Pattern
```
Create a VERY THIN horizontal banner strip. The content should only occupy
a thin horizontal band in the CENTER of the image. Add large WHITE EMPTY
SPACE above and below the content strip.

The thin content strip contains: LEFT: "[Title]" text in bold black brush
calligraphy. RIGHT: A small coral-red koi fish with [ACTION]. BOTTOM of
strip: A very simplified thin line of blue/teal ukiyo-e waves. A few pink
cherry blossom petals scattered around.

The actual illustrated content should be compressed into a THIN HORIZONTAL
STRIP only about 20% of the total image height. The rest should be pure
white empty space. Ukiyo-e Japanese watercolor style.
```

### Creating Matching Banners
To maintain visual consistency across modals:
1. Generate ONE base banner with the thin strip technique
2. Use `edit_image` to create variations (change text, modify fish action)
3. The edit tool preserves composition better than generating from scratch

**Note:** `edit_image` cannot resize images - it regenerates at similar dimensions.

### Fish Actions by Context
| Modal | Fish Action | Visual Treatment |
|-------|-------------|------------------|
| New Evolution Run | Swimming with golden glow/sparkles | Evolutionary, transformative |
| API Credentials | Holding golden key | Topic-relevant prop |
| Settings | Holding gear | Topic-relevant prop |

**Avoid:** Fire/flames trailing from fish - reads as "on fire" rather than "evolutionary"
**Prefer:** Golden glow, sparkles, energy aura - reads as transformation/magic

### Close Button Visual Centering
Mathematical center (50%) ≠ visual center when content has asymmetric weight.

For banners with waves at bottom (heavy visual weight):
- Use fixed pixel positioning: `top: 30px` for 90px banner
- Aligns with visual center of text/content, not geometric center
- Approximately 33% from top, not 50%
