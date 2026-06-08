# KLayout Plugin: Netlist Import

<!--
[![Watch the demo](doc/screenshot-demo-video.gif)](https://youtube.com/watch/v=TODO)
-->

* Import you netlist and place cell instances (e.g. Pcells)
   
This add-on can be installed through [KLayout](https://klayout.de) package manager, [see installation instructions here](#installation-instructions)

## Usage

### Import a netlist

1. Create new layout
2. Middle click your TOP Cell
3. Open importer by clicking *File*→*Import*→*Netlist* in the main menu


## Installation using KLayout Package Manager

<a id="installation-instructions"></a>

1. From the main menu, click *Tools*→*Manage Packages* to open the package manager
2. Locate the `Netlist Import`, double-click it to select for installation, then click *Apply*
3. Review and close the package installation report
4. Confirm macro execution

## Acknoledgements

Using `netlist_parser.py` by Rohan Chadhury (https://github.com/rohaansch/netlist-parser), licensed under MIT license.
