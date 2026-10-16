package org.auscope.nvcl.server.vo;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonProperty.Access;


public class SpectralDataVo {
	
    private int sampleNo;
    
    @JsonProperty(access = Access.WRITE_ONLY)
    private byte[] spectraldata;
        
    
	public int getSampleNo() {
		return sampleNo;
	}
	public void setSampleNo(int sampleNo) {
		this.sampleNo = sampleNo;
	}
	
	public float[] getFloatspectraldata() {
		float[] specfloats = new float[spectraldata.length/4];
		for (int i=0;i<spectraldata.length;i+=4){
			ByteBuffer buffer = ByteBuffer.wrap(spectraldata,i,4).order(ByteOrder.LITTLE_ENDIAN);
			specfloats[i/4]= buffer.getFloat();
		}
		return specfloats;
	}

	public void setSpectraldata(byte[] spectraldata) {
		this.spectraldata = spectraldata;
	}
	@JsonIgnore
	public byte[] getSpectraldata() {
		return spectraldata;
	}
    

}
