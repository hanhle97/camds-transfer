# Semicomponent and Application discovery — 2026-09-06

Status: UI controls verified after reconnecting to the replacement tab. Save/readback persistence remains untested.

## Verified UI observation

- Create > MDS > Component > Create opens `#/createComponent/detail?mdsType=1&type=create`.
- Toolbar exposes an image with accessible description `添加半成品部件` and help text **Add SemiComponent** (exact capitalization).
- Adjacent controls observed: Add Component, Add MDS or Module, Delete.
- Verified DOM selector: `img[title="Add SemiComponent"]`, alt `添加半成品部件`.
- Clicking inserts and selects a new Semicomponent directly under the selected Component, without a reference dialog.
- Wait for loading to finish and Details to show Type Semicomponent; stale previous-node details remain briefly visible.

## Semicomponent form

Scope fields to `get_by_role("tabpanel", name="Details", exact=True)` and `.el-form-item:has(> .el-form-item__label:text-is("LABEL"))`.

| Label | Observed control |
| --- | --- |
| Article Name | Required text input, maxlength 100 |
| Article Name(Foreign) | Text input, maxlength 100 |
| Semicomponent No. | Text input, maxlength 50 |
| CICES product identification | Text input |
| Specific weight | Input disabled with unit `-`; enabled after selecting kg/m |
| Mass | Editable input, initial 0; units mg/g/kg |
| Remark | Textarea |

No Quantity field appeared under this Component. Specific weight dropdown opens via the field's `button.el-dropdown__caret-button`, not its main unit button. Options: `-`, `kg/m`, `kg/m²`, `kg/m³` in `li.el-dropdown-menu__item`. Do not hardcode dynamic dropdown IDs. Toolbar on the selected Semicomponent exposes Add SemiComponent, Add Metarial, Add MDS or Module; nested insertion was not tested. Common Save locator: `get_by_role("button", name="Save", exact=True)`.

## Application

## Follow-up: Material attached under Semicomponent

Verified live by selecting `Semicomponent_CA_7_10096616`, clicking Add Metarial, searching exact existing test Material `CA_8_55095125/0.01`, selecting its sole result and clicking Confirm. The tree shows the new Material directly under that Semicomponent (another reference to the same Material remains under the root).

**The attached Material form exposes Proportion, not Mass.** In contrast, the same Material directly under a Component exposed Mass earlier. The Semicomponent itself under the Component still has its own Mass field.

Observed Proportion modes in the Material Details tab:

| Mode | Native radio value | Initial controls |
| --- | --- | --- |
| From / To / Average | 1 | Selected; two editable text inputs both 0; Average 0% |
| Fixed | 2 | One text input initially disabled, value 0, percent unit |
| Rest | 3 | Rest0%; no numeric text input |

Scope to Details and its Proportion field/radiogroup. Radio wrappers are `label.el-radio`; observed selectors are `label.el-radio:has(input[value="1"])`, value 2 for Fixed, value 3 for Rest. Read `aria-checked` on the wrapper. Numeric fields are `input[type="text"]` scoped inside the chosen wrapper. This follow-up inspected the default modes without switching them or entering values.

Import consequence: select the relation's input by parent type. Component → Material uses Mass; Semicomponent → Material uses percentage/range/rest. Do not reuse the current mass-only Material attachment path for Semicomponents. No Save was clicked, and persistence is not established.

## Application details

Application is a Material **tab with per-substance rows**, not a Component textbox. The Iron test Material had only Details. Published Material `CA_8_37045/1` (Leaded Yellow Brass, Copper alloys) attached under the Component exposed Application, with Nickel %(MAX) 1 and Lead %(MAX) 2.5. These cases prove conditional visibility but not all eligibility rules or thresholds.

1. Select the Material and verify its identity before proceeding.
2. Click `get_by_role("tab", name="Application", exact=True)`.
3. Scope `get_by_role("tabpanel", name="Application", exact=True)`.
4. Match a unique row by exact English Name cell (e.g. Lead). Headers: Chinese Name, English Name, %(MAX), Application. No CAS column is shown.
5. Click its Application cell text, initially `none`. DOM: `td > div.cell > span > font[color="blue"]`; span has pointer cursor. This is not a textbox/button. Resolve the Application column by its header for a generic implementation.
6. Wait for exact dialog name `prohibited substance application standard`.
7. Options are `label.el-radio` with role radio, and native child `input` values. Example scoped selector: `label.el-radio:has(input[value="3"])`. Cross-check code and displayed description; never assume IMDS and CAMDS codes match.
8. Exact dialog buttons: Cancel and Confirm. Only Cancel was exercised; the table remained `none` afterward.

### Observed option values

Lead options in this material:

| Value | Displayed option (long options abbreviated) |
| --- | --- |
| 62 | 8(g)(ii-i): flip chip connection, technology node 90 nm or larger (EU) |
| 63 | 8(g)(ii-ii): single die 300 mm2 or larger (EU) |
| 64 | 8(g)(ii-iii): stacked die / silicon interposer 300 mm2 or larger (EU) |
| 3 | Alloying element in copper (Pb≤4%）（CN/EU) |
| 12 | Solder in electronic circuit boards and other electric applications(CN) |
| 17 | (8e)-Lead in high melting temperature type solders (i.e. lead-based alloys containing 85 % by weight or more lead)(EU) |
| 28 | Other application (potentially prohibited) |

Value 3 opened already checked (`aria-checked=true`, class `is-checked`) although the table was `none`. A checked default is not proof of an applied or saved Application.

Nickel options:

| Value | Displayed option |
| --- | --- |
| 37 | Component of a surface likely to be routinely touched (eg. handles and buckles), that have a nickel release rate exceeding 0.5μg/cm2/week. |
| 38 | Other application (Surface not routinely touched or nickel release rate < 0.5μg/cm2/week) |
| 39 | Not applicable |

No Nickel option was checked initially. Options depend on the substance and may change. These observations are not legal applicability advice.

## Test state and implementation boundary

Unsaved discovery tree: Component `CA_5_158481046`, Semicomponent `CA_7_10096616`, existing Iron test Material, and the published brass reference. Changed Specific weight unit to kg/m to verify input enablement. No Application Confirm, Save, Delete, Send or Submit was clicked. The tab remains on Application for follow-up. Do not reload a type=create URL to verify persistence.

Importer support has not been enabled. Remaining verification: Semicomponent mass/density persistence and Application Confirm → Save → Search/View readback. Match parser substance application data to the Material-context row; reject ambiguity and unvalidated code mappings instead of accepting defaults.


## Follow-up: second wizard step for polymer classifications — 2026-09-06

Observed while attempting a live Create with classification `5.4.3` (Other
duromers). After choosing the classification and pressing Next, CAMDS does not
open the editor. It shows a **second dialog, also named "Creation of a new
material"**, which composes the material name from ISO 1043 dropdowns:

> The selected material classification permits creation of Name and Symbol field
> entries using dropdown selections derived from the CAMDS Basic Polymer List.

| Control | Standard |
| --- | --- |
| Select | Basic polymers ISO 1043-1 or GB/T 1844.1 |
| Select | Fillers/reinforcing materials ISO 1043-2 or GB/T 1844.2 |
| Select + `[%]` | Plasticizers ISO 1043-3 or GB/T 1844.3 (optional) |
| checkbox | Flame retardants ISO 1043-4 or GB/T 1844.4 (optional) |
| textbox | Composed symbol, adaptable by hand |

Buttons: Cancel, Next. The dialog explains that materials with more than one
filler need the symbol edited manually, e.g. `PA6-(GF15+MD10)`.

The MDS ID is allocated before this dialog appears, so cancelling still consumes
one. Both dialogs share the same accessible name; they are told apart by the
text "Composed symbol".

**Importer consequence.** `1.1.1` reaches the editor directly, which is why it
was the only classification exercised end to end. A polymer classification stops
at this step: composing an ISO 1043 symbol is a data decision, and a wrong
symbol is wrong material data. The importer therefore refuses here instead of
guessing. Not yet observed: which classifications trigger it, whether Next
accepts empty selections, and how the composed symbol relates to the parser's
material name (`PA6-GF35 FR` already looks like such a symbol).
