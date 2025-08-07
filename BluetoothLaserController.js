class BluetoothLaserController {
    constructor() {
        this.device = null;
        this.controlCharacteristic = null;
    }
    
    async connect() {
        this.device = await navigator.bluetooth.requestDevice({
            filters: [{ namePrefix: 'LaserCube' }],
            optionalServices: ['6e400001-b5a3-f393-e0a9-e50e24dcca9e']
        });
        
        const server = await this.device.gatt.connect();
        const service = await server.getPrimaryService('6e400001-b5a3-f393-e0a9-e50e24dcca9e');
        this.controlCharacteristic = await service.getCharacteristic('6e400002-b5a3-f393-e0a9-e50e24dcca9e');
    }
    
    async sendLaserCommand(x, y, r, g, b, blanking = false) {
        // LaserCube protocol: 16-bit coordinates, 8-bit colors
        const command = new ArrayBuffer(8);
        const view = new DataView(command);
        
        view.setUint16(0, (x + 1) * 32767, true);  // X: -1 to 1 → 0-65535
        view.setUint16(2, (y + 1) * 32767, true);  // Y: -1 to 1 → 0-65535
        view.setUint8(4, r);                       // Red: 0-255
        view.setUint8(5, g);                       // Green: 0-255
        view.setUint8(6, b);                       // Blue: 0-255
        view.setUint8(7, blanking ? 0 : 255);      // Blanking control
        
        await this.controlCharacteristic.writeValue(command);
    }
}
