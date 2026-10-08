package org.auscope.nvcl.server.vo;

import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Iterator;

public class SpectralDataCollectionVo {

	private ArrayList<SpectralDataVo> SpectralDataCollection;

	public SpectralDataCollectionVo() {
		SpectralDataCollection = new ArrayList<SpectralDataVo>();
	}

	public SpectralDataCollectionVo(ArrayList<SpectralDataVo> spectralDataCollection) {
		this.setSpectralDataCollection(spectralDataCollection);
	}

	public ArrayList<SpectralDataVo> getSpectralDataCollection() {
		return SpectralDataCollection;
	}

	public void setSpectralDataCollection(ArrayList<SpectralDataVo> spectralDataCollection) {
		SpectralDataCollection = spectralDataCollection;
	}

	public void mergenewSpectraldata(ArrayList<SpectralDataVo> spectralDataCollection) {
		if (SpectralDataCollection.isEmpty())
			setSpectralDataCollection(spectralDataCollection);
		else {
			for (Iterator<SpectralDataVo> it = spectralDataCollection.iterator(); it.hasNext();) {
				SpectralDataVo specdata = it.next();
				boolean merged = false;
				for (Iterator<SpectralDataVo> it2 = SpectralDataCollection.iterator(); it2.hasNext();) {
					SpectralDataVo existingspecdata = it2.next();
					if (existingspecdata.getSampleNo() == specdata.getSampleNo()) {
						existingspecdata.setSpectraldata(ByteBuffer
								.allocate(existingspecdata.getSpectraldata().length + specdata.getSpectraldata().length)
								.put(existingspecdata.getSpectraldata()).put(specdata.getSpectraldata()).array());
						merged = true;
					}
				}
				if (merged != true) {
					SpectralDataCollection.add(specdata);
				}
			}
		}
	}

}
