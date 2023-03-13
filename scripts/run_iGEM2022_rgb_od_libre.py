from opentrons import protocol_api

# utils

plate_96_wells = [
'A1','A2','A3','A4','A5','A6','A7','A8','A9','A10','A11','A12',
'B1','B2','B3','B4','B5','B6','B7','B8','B9','B10','B11','B12',
'C1','C2','C3','C4','C5','C6','C7','C8','C9','C10','C11','C12',
'D1','D2','D3','D4','D5','D6','D7','D8','D9','D10','D11','D12',
'E1','E2','E3','E4','E5','E6','E7','E8','E9','E10','E11','E12',
'F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12',
'G1','G2','G3','G4','G5','G6','G7','G8','G9','G10','G11','G12',
'H1','H2','H3','H4','H5','H6','H7','H8','H9','H10','H11','H12'
]

def liquid_transfer(pipette, volume, source, destination, asp_rate:float=0.5, disp_rate:float=1.0, blow_out:bool=True, touch_tip:bool=False, mix_before:float=0.0, mix_after:float=0.0, mix_reps:int=3, new_tip:bool=True, drop_tip:bool=True):
    if new_tip:
        pipette.pick_up_tip()
    if mix_before > 0:
        pipette.mix(mix_reps, mix_before, source)
    pipette.aspirate(volume, source, rate=asp_rate)
    pipette.dispense(volume, destination, rate=disp_rate)
    if mix_after > 0:
        pipette.mix(mix_reps, mix_after, destination)
    if blow_out: 
        pipette.blow_out()
    if touch_tip:
        pipette.touch_tip()
    if drop_tip:
        pipette.drop_tip() 

# calibration clases from PUDU

class Calibration():
    """
    Creates a ptopocol template for calibration with default values.

    ...

    Attributes
    ----------   
    aspiration_rate: float
        Aspiration rate in micro liters per second (uL/s). By default 0.5 uL/s.
    dispense_rate: float
        Dispense rate in micro liters per second (uL/s). By default 1 uL/s.
    tiprack_labware: str
        Labware to use as tiprack. By default opentrons_96_tiprack_300ul.
    tiprack_position: int
        Deck position for tiprack. By default 9.
    pipette: str
        Pipette to use, can be a p300 and p1000. By default p300_single_gen2.
    pipette_position: str
        Deck position for pipette. By default left.
    calibration_plate_labware: str
        Labware to use as calibration plate. By default corning_96_wellplate_360ul_flat.
    calibration_plate_position: int
        Deck position for calibration plate. By default 7.
    use_temperature_module: bool    
        Whether to use temperature module or not. By default True.
    tube_rack_labware: str
        Labware to use as tube rack. By default opentrons_24_aluminumblock_nest_1.5ml_snapcap.
    tube_rack_position: int
        Deck position for tube rack. By default 1.
    use_falcon_tubes: bool
        Whether to use falcon tubes or not. By default False.
    falcon_tube_rack_labware: str   
        Labware to use as falcon tube rack. By default opentrons_6_tuberack_falcon_50ml_conical.
    falcon_tube_rack_position: int
        Deck position for falcon tube rack. By default 2.
    """
    def __init__(self, 
        aspiration_rate:float=0.5,
        dispense_rate:float=1,
        tiprack_labware:str='opentrons_96_tiprack_300ul',
        tiprack_position:int=9,
        pipette:str='p300_single_gen2',
        pipette_position:str='left',
        calibration_plate_labware:str='corning_96_wellplate_360ul_flat',
        calibration_plate_position:int=7,
        use_temperature_module:bool=True,
        tube_rack_labware:str='opentrons_24_aluminumblock_nest_1.5ml_snapcap',
        tube_rack_position:int=1,
        use_falcon_tubes:bool=False,
        falcon_tube_rack_labware:str='opentrons_6_tuberack_falcon_50ml_conical',
        falcon_tube_rack_position:int=2,
        ):

        self.aspiration_rate = aspiration_rate
        self.dispense_rate = dispense_rate
        self.tiprack_labware = tiprack_labware
        self.tiprack_position = tiprack_position
        self.pipette = pipette
        self.pipette_position = pipette_position
        self.calibration_plate_labware = calibration_plate_labware
        self.calibration_plate_position = calibration_plate_position
        self.use_temperature_module = use_temperature_module
        self.tube_rack_labware = tube_rack_labware
        self.tube_rack_position = tube_rack_position
        self.use_falcon_tubes = use_falcon_tubes
        self.falcon_tube_rack_labware = falcon_tube_rack_labware
        self.falcon_tube_rack_position = falcon_tube_rack_position

class iGEM_rgb_od(Calibration):
    """
    Creates a ptopocol to calibrate GFP using fluorescein (MEFL), sulforhodamine 101, cascade blue and OD600 using nanoparticles.
    Refer to: https://old.igem.org/wiki/images/a/a4/InterLab_2022_-_Calibration_Protocol_v2.pdf

    Attributes
    ----------   
    aspiration_rate: float
        Aspiration rate in micro liters per second (uL/s). By default 0.5 uL/s.
    dispense_rate: float
        Dispense rate in micro liters per second (uL/s). By default 1 uL/s.
    tiprack_labware: str
        Labware to use as tiprack. By default opentrons_96_tiprack_300ul.
    tiprack_position: int
        Deck position for tiprack. By default 9.
    pipette: str
        Pipette to use, can be a p300 and p1000. By default p300_single_gen2.
    pipette_position: str
        Deck position for pipette. By default left.
    calibration_plate_labware: str
        Labware to use as calibration plate. By default corning_96_wellplate_360ul_flat.
    calibration_plate_position: int
        Deck position for calibration plate. By default 7.
    use_temperature_module: bool    
        Whether to use temperature module or not. By default True.
    tube_rack_labware: str
        Labware to use as tube rack. By default opentrons_24_aluminumblock_nest_1.5ml_snapcap.
    tube_rack_position: int
        Deck position for tube rack. By default 1.
    use_falcon_tubes: bool
        Whether to use falcon tubes or not. By default False.
    falcon_tube_rack_labware: str   
        Labware to use as falcon tube rack. By default opentrons_6_tuberack_falcon_50ml_conical.
    falcon_tube_rack_position: int
        Deck position for falcon tube rack. By default 2.
    """
    def __init__(self,
        *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self, protocol: protocol_api.ProtocolContext):

        #Labware
        tiprack = protocol.load_labware(self.tiprack_labware, f'{self.tiprack_position}')
        pipette = protocol.load_instrument(self.pipette, self.pipette_position, tip_racks=[tiprack])
        plate = protocol.load_labware(self.calibration_plate_labware, self.calibration_plate_position)
        if self.use_temperature_module:
            temperature_module = protocol.load_module('Temperature Module', self.tube_rack_position)
            tube_rack = temperature_module.load_labware(self.tube_rack_labware)
        else:
            tube_rack = protocol.load_labware(self.tube_rack_labware, self.tube_rack_position)
        if self.use_falcon_tubes:
            falcon_tube_rack = protocol.load_labware(self.falcon_tube_rack_labware, self.falcon_tube_rack_position)
        #Protocol
        #set deck
        #add calibrants
        sulforhodamine_1x = tube_rack['A1']
        fluorescein_1x = tube_rack['A2']
        cascade_blue_1x = tube_rack['A3']
        microspheres_1x = tube_rack['A4']
        #add dilution buffers in tube rack
        pbs_1 = tube_rack['B1']
        pbs_2 = tube_rack['B2']
        pbs_3 = tube_rack['B3']
        pbs_4 = tube_rack['B4']
        water_1 = tube_rack['C1']
        water_2 = tube_rack['C2']
        water_3 = tube_rack['C3']
        water_4 = tube_rack['C4']
        #if using falcon, remap pbs and water
        if self.use_falcon_tubes:
            #add dilution buffers in falcon tube rack
            pbs_falcon = falcon_tube_rack['A1']
            water_falcon = falcon_tube_rack['A2']
            pbs_1 = pbs_falcon
            pbs_2 = pbs_falcon
            pbs_3 = pbs_falcon
            pbs_4 = pbs_falcon
            water_1 = water_falcon
            water_2 = water_falcon
            water_3 = water_falcon
            water_4 = water_falcon
        #dispense PBS
        pipette.pick_up_tip()
        for well in plate_96_wells[1:12]:
            liquid_transfer(pipette, 100, pbs_1, plate[well], self.aspiration_rate, self.dispense_rate, new_tip=False, drop_tip=False)
        for well in plate_96_wells[13:24]:
            liquid_transfer(pipette, 100, pbs_2, plate[well], self.aspiration_rate, self.dispense_rate, new_tip=False, drop_tip=False)
        for well in plate_96_wells[25:36]:
            liquid_transfer(pipette, 100, pbs_3, plate[well], self.aspiration_rate, self.dispense_rate, new_tip=False, drop_tip=False)
        for well in plate_96_wells[37:48]:
            liquid_transfer(pipette, 100, pbs_4, plate[well], self.aspiration_rate, self.dispense_rate, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        #dispense water
        pipette.pick_up_tip()
        for well in plate_96_wells[49:60]:
            liquid_transfer(pipette, 100, water_1, plate[well], self.aspiration_rate, self.dispense_rate, new_tip=False, drop_tip=False)
        for well in plate_96_wells[61:72]:
            liquid_transfer(pipette, 100, water_2, plate[well], self.aspiration_rate, self.dispense_rate, new_tip=False, drop_tip=False)
        for well in plate_96_wells[73:84]:
            liquid_transfer(pipette, 100, water_3, plate[well], self.aspiration_rate, self.dispense_rate, new_tip=False, drop_tip=False)
        for well in plate_96_wells[85:96]:
            liquid_transfer(pipette, 100, water_4, plate[well], self.aspiration_rate, self.dispense_rate, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        #dispense fluorescein
        liquid_transfer(pipette, 200, fluorescein_1x, plate['A1'], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4)
        liquid_transfer(pipette, 200, fluorescein_1x, plate['B1'], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4)
        #dispense sulforhodamine 101
        liquid_transfer(pipette, 200, sulforhodamine_1x, plate['C1'], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4)
        liquid_transfer(pipette, 200, sulforhodamine_1x, plate['D1'], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4)
        #dispense cascade blue
        liquid_transfer(pipette, 200, cascade_blue_1x, plate['E1'], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4)
        liquid_transfer(pipette, 200, cascade_blue_1x, plate['F1'], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4)
        #dispense microspheres nanoparticles
        liquid_transfer(pipette, 200, microspheres_1x, plate['G1'], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4)
        liquid_transfer(pipette, 200, microspheres_1x, plate['H1'], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4)
        #fluorescein serial dilutions
        pipette.pick_up_tip()
        for i in range(0,11):
            liquid_transfer(pipette, 100, plate[plate_96_wells[i]], plate[plate_96_wells[i+1]], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        pipette.pick_up_tip()
        for i in range(12,23):
            liquid_transfer(pipette, 100, plate[plate_96_wells[i]], plate[plate_96_wells[i+1]], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        #sulforamine serial dilution
        pipette.pick_up_tip()
        for i in range(24,35):
            liquid_transfer(pipette, 100, plate[plate_96_wells[i]], plate[plate_96_wells[i+1]], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        pipette.pick_up_tip()
        for i in range(36,47):
            liquid_transfer(pipette, 100, plate[plate_96_wells[i]], plate[plate_96_wells[i+1]], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        #cascade blue serial dilution
        pipette.pick_up_tip()
        for i in range(48,59):
            liquid_transfer(pipette, 100, plate[plate_96_wells[i]], plate[plate_96_wells[i+1]], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        pipette.pick_up_tip()
        for i in range(60,71):
            liquid_transfer(pipette, 100, plate[plate_96_wells[i]], plate[plate_96_wells[i+1]], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        #nanoparticles serial dilutions
        pipette.pick_up_tip()
        for i in range(72,83):
            liquid_transfer(pipette, 100, plate[plate_96_wells[i]], plate[plate_96_wells[i+1]], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        pipette.pick_up_tip()
        for i in range(84,95):
            liquid_transfer(pipette, 100, plate[plate_96_wells[i]], plate[plate_96_wells[i+1]], self.aspiration_rate, self.dispense_rate, mix_before=200, mix_reps=4, new_tip=False, drop_tip=False)
        pipette.drop_tip()
        #END


# metadata

metadata = {
'protocolName': 'iGEM GFP OD600 calibration',
'author': 'Gonzalo Vidal <g.a.vidal-pena2@newcastle.ac.uk>',
'description': 'Protocol to perform serial dilutions of fluorescein and nanoparticles for calibration',
'apiLevel': '2.13'}

# run protocol, redefine protocol if needed

def run(protocol= protocol_api.ProtocolContext):

    pudu_calibration = iGEM_rgb_od()
    pudu_calibration.run(protocol)