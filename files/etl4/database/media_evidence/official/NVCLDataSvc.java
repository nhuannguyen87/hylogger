package org.auscope.nvcl.server.service;

import java.awt.Color;
import java.awt.geom.AffineTransform;
import java.awt.image.AffineTransformOp;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import com.azure.storage.blob.BlobClient;
import com.azure.storage.blob.BlobContainerClient;
import com.azure.storage.blob.models.BlobItem;
import com.azure.storage.blob.specialized.BlockBlobClient;
import com.drew.imaging.ImageMetadataReader;
import com.drew.imaging.ImageProcessingException;
import com.drew.metadata.Metadata;
import com.drew.metadata.MetadataException;
import com.drew.metadata.jpeg.JpegCommentDirectory;
import com.drew.metadata.jpeg.JpegDirectory;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.auscope.nvcl.server.dao.DomainDataDao;
import org.auscope.nvcl.server.dao.DownSampledClassDataDao;
import org.auscope.nvcl.server.dao.DownSampledFloatDataDao;
import org.auscope.nvcl.server.dao.LogExtentsDao;
import org.auscope.nvcl.server.dao.NVCLDataSvcDao;
import org.auscope.nvcl.server.util.Utility;
import org.auscope.nvcl.server.vo.AlgorithmCollectionVo;
import org.auscope.nvcl.server.vo.AlgorithmOutputVersionVo;
import org.auscope.nvcl.server.vo.AveragedFloatDataVo;
import org.auscope.nvcl.server.vo.BinnedClassDataVo;
import org.auscope.nvcl.server.vo.ClassDataVo;
import org.auscope.nvcl.server.vo.ClassificationsCollectionVo;
import org.auscope.nvcl.server.vo.DatasetCollectionVo;
import org.auscope.nvcl.server.vo.DepthRangeVo;
import org.auscope.nvcl.server.vo.DomainDataCollectionVo;
import org.auscope.nvcl.server.vo.DomainDataVo;
import org.auscope.nvcl.server.vo.DomainLogCollectionVo;
import org.auscope.nvcl.server.vo.DomainLogVo;
import org.auscope.nvcl.server.vo.FloatDataVo;
import org.auscope.nvcl.server.vo.ImageDataVo;
import org.auscope.nvcl.server.vo.ImageLogCollectionVo;
import org.auscope.nvcl.server.vo.LogCollectionVo;
import org.auscope.nvcl.server.vo.LogDetailsVo;
import org.auscope.nvcl.server.vo.LogExtentsVo;
import org.auscope.nvcl.server.vo.MaskDataVo;
import org.auscope.nvcl.server.vo.ProfDataCollectionVo;
import org.auscope.nvcl.server.vo.ProfLogCollectionVo;
import org.auscope.nvcl.server.vo.SectionVo;
import org.auscope.nvcl.server.vo.SpectralDataCollectionVo;
import org.auscope.nvcl.server.vo.SpectralLogCollectionVo;
import org.auscope.nvcl.server.vo.TraySectionsVo;
import org.jfree.chart.ChartFactory;
import org.jfree.chart.JFreeChart;
import org.jfree.chart.LegendItem;
import org.jfree.chart.LegendItemCollection;
import org.jfree.chart.axis.NumberAxis;
import org.jfree.chart.plot.PlotOrientation;
import org.jfree.chart.plot.XYPlot;
import org.jfree.chart.renderer.xy.StackedXYBarRenderer;
import org.jfree.chart.renderer.xy.StandardXYBarPainter;
import org.jfree.chart.renderer.xy.XYBarRenderer;
import org.jfree.chart.renderer.xy.XYDotRenderer;
import org.jfree.chart.renderer.xy.XYLineAndShapeRenderer;
import org.jfree.chart.renderer.xy.XYShapeRenderer;
import org.jfree.data.xy.DefaultTableXYDataset;
import org.jfree.data.xy.IntervalXYDataset;
import org.jfree.data.xy.XYBarDataset;
import org.jfree.data.xy.XYSeries;
import org.jfree.data.xy.XYSeriesCollection;
import org.jfree.ui.RectangleEdge;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.RowCallbackHandler;

/**
 * Service class that provide action call to Data Access Object (DAO) methods,
 * receiving the output from the DAO and process the output data before passing
 * it back to the controller.
 * <p>
 * It also provide the getter and setter for the data access object beans define
 * in applicationContext.xml
 *
 * @author Florence Tan
 */

public class NVCLDataSvc {

	private static final Logger logger = LogManager.getLogger(NVCLDataSvc.class);

    private NVCLDataSvcDao nvclDataSvcDao;
    private NVCLBlobStoreAccessSvc nvclBlobStoreAccessSvc;
    private DownSampledClassDataDao downSampledClassDataDao;
    private DownSampledFloatDataDao downSampledFloatDataDao;
    private DomainDataDao domainDataDao;
    private LogExtentsDao logExtentsDao;

    /**
     * Configure the NvclDataSvcDao (Data Access Object)
     *
     * @param nvclDataSvcDao
     *            data access object injected via applicationContext.xml
     */
    @Autowired
    public void setNvclDataSvcDao(NVCLDataSvcDao nvclDataSvcDao) {
        this.nvclDataSvcDao = nvclDataSvcDao;
    }

    /**
     * Configure the NVCLBlobStoreAccessSvc 
     *
     * @param nvclBlobStoreAccessSvc
     *            data access object
     */
    public void setNVCLBlobStoreAccessSvc(NVCLBlobStoreAccessSvc nvclBlobStoreAccessSvc) {
        this.nvclBlobStoreAccessSvc = nvclBlobStoreAccessSvc;
    }


    /**
     * Configure the DownSampledClassDataDao (Data Access Object)
     *
     * @param DownSampledClassDataDao
     *            data access object injected via applicationContext.xml
     */
    @Autowired
    public void setdownSampledClassDataDao(DownSampledClassDataDao downSampledClassDataDao) {
        this.downSampledClassDataDao = downSampledClassDataDao;
    }

    /**
     * Configure the DownSampledFloatDataDao (Data Access Object)
     *
     * @param DownSampledFloatDataDao
     *            data access object injected via applicationContext.xml
     */
    @Autowired
    public void setdownSampledFloatDataDao(DownSampledFloatDataDao downSampledFloatDataDao) {
        this.downSampledFloatDataDao = downSampledFloatDataDao;
    }


    /**
     * Configure the GetDomainDataDao (Data Access Object)
     *
     * @param GetDomainDataDao
     *            data access object injected via applicationContext.xml
     */
    @Autowired
    public void setGetDomaindataDao(DomainDataDao domainDataDao) {
        this.domainDataDao = domainDataDao;
    }
    
    /**
     * Configure the LogExtentsDao (Data Access Object)
     *
     * @param LogExtentsDao
     *            data access object injected via applicationContext.xml
     */
    @Autowired
    public void setLogExtentsDao(LogExtentsDao logExtentsDao) {
        this.logExtentsDao = logExtentsDao;
    }
    

    /**
     * Getting the log type and log name from logs table base on the unique log
     * id
     *
     * @param logId
     *            unique log id for retrieving detail from logs table
     * @return LogDetailsVo the log details value object that consists of log
     *         type and log name from logs table. logType determine which data
     *         access object to call and logName is use as chart title
     */
    public LogDetailsVo getLogDetails(String logId) throws SQLException,
            DataAccessException {
        return nvclDataSvcDao.getLogDetails(logId);
    }


    /**
     * Getting log details for a list of log id
     *
     * @param logIdList     a list of log id
     * @return List<LogDetailsVo> return a list of log details value object
     */
    public List<LogDetailsVo> getLogDetails(String[] logIdList) {
        return nvclDataSvcDao.getLogDetails(logIdList);
    }


    /**
     * Getting the Domain Chart depth range for a scalar selection log id
     *
     * @param logId
     *            unique log id for retrieving detail from logs table
     * @return DomainDepthRangeVo the log details value object that consists of
     *         start start and end range measures.
     */
    public float[] getDomainChartDepthRange(String log_id)
            throws DataAccessException, SQLException {
        logger.debug("getDomainChartDepthRange(" + log_id + ") start ...");
        return nvclDataSvcDao.getDomainChartDepthRange(log_id);
    }

        /**
     * Getting the Domain depth range for a dataset id
     *
     * @param dataset_id
     *            unique log id for retrieving detail from logs table
     * @return DomainDepthRangeVo the log details value object that consists of
     *         start start and end range measures.
     */
    public DepthRangeVo getDatasetDepthRange(String dataset_id)
            throws DataAccessException, SQLException {
        logger.debug("getDomainChartDepthRange(" + dataset_id + ") start ...");
        return nvclDataSvcDao.getDatasetDepthRange(dataset_id);
    }

    /**
     * Getting the dataset id and dataset name from datasets table base on the
     * holeidentifier
     *
     * @param holeIdentifier
     *            holeIdentifier is the same as the borehole id from the
     *            borehole header info return from the geoserver sfs
     * @return List a List of DatasetCollectionVo value object that consists of
     *         dataset id and dataset name from datasets table.
     */
    public DatasetCollectionVo getDatasetCollection(String holeIdentifier) {
        return nvclDataSvcDao.getDatasetCollection(holeIdentifier);
    }

    /**
     * Getting the dataset id and dataset name from datasets table by datasetid
     *
     * @param datasetId
     *            datasetID is the identifier of a nvcl dataset.
     * @return List a List of DatasetCollectionVo value object that consists of
     *         dataset id and dataset name from datasets table.
     */
    public DatasetCollectionVo getDatasetCollectionbyDatasetId(String datasetId) {
    	return nvclDataSvcDao.getDatasetCollectionbyDatasetId(datasetId);
    }

      /**
     * Get single dataset from table datasetsets by its datasets name
     *
     * @param datasetName
     *            datasetName is the human readable name of a nvcl dataset.
     * @return DatasetCollectionVo value object that consists of a collection of
     *         dataset id and dataset name from datasets table.
     */
    public DatasetCollectionVo getDatasetCollectionbyDatasetName(String datasetName) {
    	return nvclDataSvcDao.getDatasetCollectionbyDatasetName(datasetName);
    }

    /**
     * Get the boreholeURI from datasets table by datasetid
     *
     * @param datasetId
     *            datasetID is the identifier of a nvcl dataset.
     * @return boreholeURI
     */
    public String getBoreholeHoleURIbyDatasetId(String datasetId){
        return nvclDataSvcDao.getBoreholeHoleURIbyDatasetId(datasetId);
    }
    /**
     * Getting the log id and log name from logs table base on the dataset id
     *
     * @param datsetID
     *            dataset id for retrieving logs id and name from logs table
     * @return List a List of LogCollectionVo value object that consists of log
     *         id and log name from logs table.
     */
    public LogCollectionVo getLogCollection(String datasetId) {
        return nvclDataSvcDao.getLogCollection(datasetId);
    }

    /**
     * Getting the list of log id and log name and sample number
     * from LOGS table filtered by dataset id and where
     * logtype=3. Order the resultSet in the following
     * order: 1.Mosaic 2.Tray Thumbnail Images 3.Tray Images 4.holeing
     * 5.anything else
     *
     * @param datasetID
     *            dataset id for retrieving a list of log_id and logName
     * @return List a List of LogCollectionMosaicVo value object consisting
     *            of logID, logName and sample number in the above order.
     */
    public ImageLogCollectionVo getImageLogCollection(String datasetId) {
        return nvclDataSvcDao.getImageLogCollection(datasetId);
    }

        /**
     * Getting the list of log id and log name and sample number
     * from LOGS table filtered by dataset id and where
     * logtype=0. 
     *
     * @param datasetID
     *            dataset id for retrieving a list of log_id and logName
     * @return List a List of LogCollectionMosaicVo value object consisting
     *            of logID, logName and sample number in the above order.
     */
    public DomainLogCollectionVo getDomainLogCollection(String datasetId) {
        return nvclDataSvcDao.getDomainLogCollection(datasetId);
    }

    /**
     * Getting the tray thumbnail image (store as BLOB data type) from IMAGELOGS
     * table
     *
     * @param logID
     *            as first primary key for retrieving a single thumbnail image
     * @param sampleNo
     *            as second primary key for retrieving a single thumbnail image
     * @return ImageLogsVo the image logs value object which consists a
     *         thumbnail image in blob data type
     */
    public ImageDataVo getImageData(String logID,String datasetid, int sampleNo, Boolean applycorrection) {
        logger.debug("getImageData start...");
        if (nvclBlobStoreAccessSvc.isConnected) {
            if (!Utility.isAlphanumericOrHyphen(datasetid)) datasetid = this.getDatasetIdfromLogId(logID);
            ImageDataVo imgdata=nvclBlobStoreAccessSvc.getImgData(datasetid,logID, sampleNo);
            if (applycorrection){
                byte[] histo = nvclDataSvcDao.getImgHistogramData(logID);
                if (histo!=null && histo.length>0) imgdata.setImgHistogramLUT(histo);
            }
            return imgdata;
        }
        else {      
            return nvclDataSvcDao.getImgData(logID, sampleNo);
        }
    }

    public byte[] getImgHistogramData(String logID) {
        return nvclDataSvcDao.getImgHistogramData(logID);
    }

    /**
     * Getting the list of sample numbers from IMAGELOGS table base on the log
     * id provided
     *
     * @param logID
     *            as primary key for retrieving a list of sample numbers
     * @param startSampleNo
     *            starting number for the range of sample number to retrieve
     * @param endSampleNo
     *            ending number for the range of sample number to retrieve
     * @return List the image logs value object which consists of a list of
     *         sample number
     */
    public List<ImageDataVo> getSampleNo(String logID, int startSampleNo,
            int endSampleNo) {
        logger.debug("getSampleNo start...");
        return nvclDataSvcDao.getSampleNo(logID, startSampleNo, endSampleNo);
    }

    /**
     * Called when log type is Class and execute the function call in
     * DownSampledClassDataDao class. Extract the record set in the returned Map, base
     * on the graphType, map to different JFreeChart dataset :
     * <p>
     * <ul>
     * <li>graphType == 1 : DefaultTableXYDataset (For Stacked Bar Chart)
     * <li>graphType == 2 || 3 : XYSeriesCollection (For Line and Scatter Chart)
     * </ul>
     * <p>
     * Add the dataset and the series' colour ArrayList into an ArrayList and
     * return it to the calling controller.
     *
     * @param logId
     *            unique log id (String)
     * @param startDepth
     *            starting point of a borehole (float)
     * @param endDepth
     *            ending point of a borehole (float)
     * @param samplingInterval
     *            sampling interval (float)
     * @param graphType
     *            type of graph, 1 for Bar Chart, 2 for Scatter Chart, 3 for
     *            Line Chart (int)
     * @return ArrayList this ArrayList consists of JFreeChart dataset and an
     *         ArrayList of series's colour
     */
    public ArrayList<Object> getdownSampledClassData(String logId, float startDepth,
            float endDepth, float samplingInterval, int graphType) {

        // initialize local variables
        String prevMineral = null;
        String currentMineral = null;
        XYSeries series = null;
        ArrayList<Integer> seriesList = new ArrayList<Integer>();
        int lastColor = 0;
        int i = 0;

        /**
         * Calling downSampledClassDataDao.execute(parameters) will return an ArrayList
         * of LogTypeOneVo type records.
         */
        List<BinnedClassDataVo> logTypeOneArr = downSampledClassDataDao.execute(logId, startDepth, endDepth,
                samplingInterval,5);
        ArrayList<Object> arrOutput = null;
        if (logTypeOneArr != null) {

            arrOutput = new ArrayList<Object>();

            if (graphType == 1) {

                DefaultTableXYDataset dataSet = new DefaultTableXYDataset();

                for (Iterator<BinnedClassDataVo> it1 = logTypeOneArr.iterator(); it1.hasNext();) {
                    // extract logTypeOneVo details
                    BinnedClassDataVo logTypeOneVo = it1.next();
                    logger.debug(logTypeOneVo.getRoundedDepth() + "\t"
                            + logTypeOneVo.getClassCount() + "\t"
                            + logTypeOneVo.getClassText() + "\t"
                            + logTypeOneVo.getColour() + "\t");
                    i++;
                    if (i == 1) {
                        prevMineral = logTypeOneVo.getClassText();
                        series = new XYSeries(logTypeOneVo.getClassText(),
                                true, false);

                    }
                    currentMineral = logTypeOneVo.getClassText();
                    if (!prevMineral.equals(currentMineral)) {
                        dataSet.addSeries(series);
                        seriesList.add(lastColor);
                        series = new XYSeries(logTypeOneVo.getClassText(),
                                true, false);
                    }

                    series.add(logTypeOneVo.getRoundedDepth(),
                            logTypeOneVo.getClassCount());
                    prevMineral = logTypeOneVo.getClassText();
                    lastColor = logTypeOneVo.getColour();
                }

                dataSet.addSeries(series);
                seriesList.add(lastColor);
                arrOutput.add(dataSet);
                arrOutput.add(seriesList);
            } else {
                XYSeriesCollection dataSet = new XYSeriesCollection();
                for (Iterator<BinnedClassDataVo> it1 = logTypeOneArr.iterator(); it1.hasNext();) {
                    // extract logTypeOneVo details
                    BinnedClassDataVo logTypeOneVo = it1.next();
                    logger.debug(logTypeOneVo.getRoundedDepth() + "\t"
                            + logTypeOneVo.getClassCount() + "\t"
                            + logTypeOneVo.getClassText() + "\t"
                            + logTypeOneVo.getColour() + "\t");
                    i++;
                    if (i == 1) {
                        prevMineral = logTypeOneVo.getClassText();
                        series = new XYSeries(logTypeOneVo.getClassText(),
                                true, false);
                    }
                    currentMineral = logTypeOneVo.getClassText();
                    if (!prevMineral.equals(currentMineral)) {
                        dataSet.addSeries(series);
                        seriesList.add(lastColor);
                        series = new XYSeries(logTypeOneVo.getClassText(),
                                true, false);
                    }

                    series.add(logTypeOneVo.getRoundedDepth(),
                            logTypeOneVo.getClassCount());
                    prevMineral = logTypeOneVo.getClassText();
                    lastColor = logTypeOneVo.getColour();
                }

                dataSet.addSeries(series);
                seriesList.add(lastColor);
                arrOutput.add(dataSet);
                arrOutput.add(seriesList);

            }

        }
        return arrOutput;

    }

    public List<BinnedClassDataVo> getdownSampledClassData(String logId, float startDepth, float endDepth, float samplingInterval,float minthreshold){
    	return  downSampledClassDataDao.execute(logId, startDepth, endDepth,samplingInterval,minthreshold);
    }
    /**
     * Called when log type is Float and execute the function call in
     * DownSampledFloatDataDao class. Extract the record set in the returned Map, base
     * on the graphType, map to different JFreeChart dataset :
     * <p>
     * <ul>
     * <li>graphType == 1 : IntervalXYDataset (For Bar Chart)
     * <li>graphType == 2 || 3 : XYSeriesCollection (For Line and Scatter Chart)
     * </ul>
     * <p>
     * Add the dataset into an ArrayList and return it to the calling
     * controller.
     *
     * @param logId
     *            unique log id (String)
     * @param startDepth
     *            starting point of a borehole (float)
     * @param endDepth
     *            ending point of a borehole (float)
     * @param samplingInterval
     *            sampling interval (float)
     * @param graphType
     *            type of graph, 1 for Bar Chart, 2 for Scatter Chart, 3 for
     *            Line Chart (int)
     * @return ArrayList this ArrayList has a JFreeChart dataset
     */
    public ArrayList<IntervalXYDataset> getdownSampledFloatDataDao(String logId, float startDepth,
            float endDepth, float samplingInterval, int graphType) {

        ArrayList<IntervalXYDataset> arrOutput = new ArrayList<IntervalXYDataset>();

        /**
         * Calling DownSampledFloatDataDao.execute(parameters) will return an ArrayList
         * of LogTypeTwoVo type records.
         */
        List<AveragedFloatDataVo> logTypeTwoArr = downSampledFloatDataDao.execute(logId, startDepth, endDepth,
                samplingInterval, 0.0f);
        logger.debug("logTypeTwoArr : " + logTypeTwoArr);

        logger.debug("depth down hole (size) : " + logTypeTwoArr.size());

        XYSeriesCollection dataSet = new XYSeriesCollection();
        XYSeries series = null;
        series = new XYSeries("Depth Down Hole");
        for (Iterator<AveragedFloatDataVo> it1 = logTypeTwoArr.iterator(); it1.hasNext();) {
            AveragedFloatDataVo logTypeTwoVo = it1.next();
            float xValue = logTypeTwoVo.getRoundedDepth();
            float yValue = logTypeTwoVo.getAverageValue();
            series.add(xValue, yValue);
            logger.debug("xvalue : " + xValue + "   yvalue : " + yValue);
        }
        dataSet.addSeries(series);

        if (graphType == 1) {
            // A dataset wrapper class that can convert any XYDataset into an
            // IntervalXYDataset
            // (so that the dataset can be used with renderers that require this
            // extended interface,
            // such as the XYBarRenderer class). This class extends
            // AbstractIntervalXYDataset.
            // XYBarDataset(XYDataset underlying, double barWidth);
            IntervalXYDataset internalXYDataset = new XYBarDataset(dataSet,
                    samplingInterval);
            arrOutput.add(internalXYDataset);
        } else {
            arrOutput.add(dataSet);
        }

        return arrOutput;
    }
    
    public List<AveragedFloatDataVo> getdownSampledFloatDataDao(String logId, float startDepth, float endDepth, float samplingInterval) {
    	return downSampledFloatDataDao.execute(logId, startDepth, endDepth, samplingInterval,0.0f);
    	
    }

    /**
     * Creating a StackedBarChart base on JFreeChart DefaultTableXYDataset
     * dataset
     *
     * @param DefaultTableXYDataset
     *            JFreeChart dataset for plotting StackedBarChart
     * @param title
     *            chart title
     * @param arrSeriesColor
     *            an ArrayList consists of series colour code in BGR
     * @param globalDepthRange
     *            [] globalDepthRange[0] is a global start depth (m)
     *            globalDepthRange[1] is a global end depth (m)
     * @param end_depth_scale
     *            global end depth scale applied to a chart
     * @return JFreeChart a stacked bar chart in JPEG format
     */
    public JFreeChart createStackedXYBarChart(DefaultTableXYDataset dataSet,
            String title, ArrayList<Integer> arrSeriesColor, float samplingInterval,
            float start_range, float end_range, int showLegend) {

        logger.debug("createStackedXYBarChart.... ");

        // customise domainAxis (y-axis)
        NumberAxis domainAxis = new NumberAxis("Depth (m)");
        domainAxis.setInverted(true);
        domainAxis.setAutoRangeIncludesZero(false);
        domainAxis.setUpperMargin(0.2);
        domainAxis.setLowerMargin(0);
        domainAxis.setRange(start_range, end_range);

        // customise rangeAxis (x-axis)
        NumberAxis rangeAxis = new NumberAxis("Sample Count");
        rangeAxis.setAutoRangeIncludesZero(true);

        // customise the StackedXYBarRenderer
        // barwidth (positive) => % to be cut out from the bar width
        // barwidth (negative) => % to be added to the bar width
        float barwidth = 1 - samplingInterval;
        StackedXYBarRenderer renderer = new StackedXYBarRenderer(barwidth);
        renderer.setDrawBarOutline(false);
        renderer.setShadowVisible(false);
        renderer.setBarPainter(new StandardXYBarPainter());

        // customise the series colour
        int j = 0;

        for (Iterator<Integer> it1 = arrSeriesColor.iterator(); it1.hasNext();) {
            Integer colorCode = it1.next();
            renderer.setSeriesPaint(j,
                    Utility.BGRColorToJavaColor(colorCode.intValue()));
            j++;
        }

        // Creating the StackedXYBarChart
        XYPlot plot = new XYPlot(dataSet, domainAxis, rangeAxis, renderer);
        plot.setOrientation(PlotOrientation.HORIZONTAL);
        plot.setNoDataMessage("No data found !!");

        JFreeChart chart = new JFreeChart(title, plot);

        chart.setBackgroundPaint(new Color(255, 255, 255, 0));
        chart.getLegend().visible = (showLegend == 0) ? false : true;
        chart.getLegend().setBackgroundPaint(new Color(255, 255, 255, 0));
        chart.getLegend().setPosition(RectangleEdge.RIGHT);
        chart.getLegend().setBorder(0, 0, 0, 0);

        return chart;
    }

    /**
     * Generating a Scatter Chart base on JFreeChart XYSeriesCollection dataset
     *
     * @param XYSeriesCollection
     *            JFreeChart dataset for plotting scatter chart
     * @param title
     *            chart title
     * @param arrSeriesColor
     *            an ArrayList storing a list of series' colour
     * @return JFreeChart a line chart in PNG format
     * @throws Exception
     */
    public JFreeChart createScatterChart(XYSeriesCollection dataSet,
            String title, ArrayList<Integer> arrSeriesColor, float samplingInterval,
            float start_range, float end_range, int showLegend)
            throws Exception {
        logger.debug("create multiple series ScatterChart.... ");
        JFreeChart chart;

        NumberAxis domainAxis = new NumberAxis("Depth (m)");
        domainAxis.setInverted(true);
        domainAxis.setAutoRangeIncludesZero(false);
        domainAxis.setRange(start_range, end_range);

        NumberAxis rangeAxis = new NumberAxis("Meter Averages");
        rangeAxis.setAutoRangeIncludesZero(false);

        XYShapeRenderer renderer = new XYShapeRenderer();
        renderer.setDrawOutlines(false);
        renderer.setAutoPopulateSeriesShape(true);

        int j = 0;
        for (Iterator<Integer> it1 = arrSeriesColor.iterator(); it1.hasNext();) {
            Integer colorCode = it1.next();
            renderer.setSeriesPaint(j,
                    Utility.BGRColorToJavaColor(colorCode.intValue()));
            j++;
        }

        XYPlot plot = new XYPlot(dataSet, domainAxis, rangeAxis, renderer);
        plot.setOrientation(PlotOrientation.HORIZONTAL);
        plot.setNoDataMessage("No data found !!");

        plot.setBackgroundPaint(Color.darkGray);

        chart = new JFreeChart(title, plot);
        chart.setBackgroundPaint(new Color(255, 255, 255, 0));
        chart.getLegend().visible = (showLegend == 0) ? false : true;
        chart.getLegend().setBackgroundPaint(new Color(255, 255, 255, 0));
        chart.getLegend().setPosition(RectangleEdge.RIGHT);
        chart.getLegend().setBorder(0, 0, 0, 0);

        return chart;

    }

    /**
     * Generating a Scatter Chart base on JFreeChart XYSeriesCollection dataset
     *
     * @param XYSeriesCollection
     *            JFreeChart dataset for plotting scatter chart
     * @param title
     *            chart title
     * @return JFreeChart a line chart in PNG format
     *
     */
    public JFreeChart createScatterChart(XYSeriesCollection dataSet,
            String title, float samplingInterval, float start_range,
            float end_range, int showLegend) throws Exception {
        logger.debug("createScatterChart.... ");

        NumberAxis domainAxis = new NumberAxis("Depth (m)");
        domainAxis.setInverted(true);
        domainAxis.setAutoRangeIncludesZero(false);
        domainAxis.setRange(start_range, end_range);

        NumberAxis rangeAxis = new NumberAxis("Meter Averages");
        rangeAxis.setAutoRangeIncludesZero(true);

        XYDotRenderer renderer = new XYDotRenderer();
        renderer.setDotWidth(6);
        renderer.setDotHeight(6);

        XYPlot plot = new XYPlot(dataSet, domainAxis, rangeAxis, renderer);
        plot.setOrientation(PlotOrientation.HORIZONTAL);
        plot.setNoDataMessage("No data found !!");
        plot.setBackgroundPaint(Color.darkGray);

        if (showLegend == 1)
            plot.setFixedLegendItems(getTransparentInvisibleLegend());

        JFreeChart chart = new JFreeChart(title, plot);
        chart.setBackgroundPaint(new Color(255, 255, 255, 0));
        chart.getLegend().visible = (showLegend == 0) ? false : true;
        chart.getLegend().setBackgroundPaint(new Color(255, 255, 255, 0));
        chart.getLegend().setPosition(RectangleEdge.RIGHT);
        chart.getLegend().setBorder(0, 0, 0, 0);

        return chart;

    }

    /**
     * Generating a Bar Chart base on JFreeChart IntervalXYDataset dataset
     *
     * @param IntervalXYDataset
     *            JFreeChart dataset for plotting bar chart
     * @param title
     *            chart title
     * @return JFreeChart a line chart in PNG format
     * @throws Exception
     */
    public JFreeChart createXYBarChart(IntervalXYDataset dataSet, String title,
            float samplingInterval, float start_range, float end_range,
            int showLegend) throws Exception {
        logger.debug("createBarChart.... ");

        NumberAxis domainAxis = new NumberAxis("Depth (m)");
        domainAxis.setInverted(true);
        domainAxis.setAutoRangeIncludesZero(false);
        domainAxis.setUpperMargin(0.2);
        domainAxis.setLowerMargin(0);
        domainAxis.setRange(start_range, end_range);

        // customise rangeAxis (x-axis)
        NumberAxis rangeAxis = new NumberAxis("Meter Averages");
        rangeAxis.setAutoRangeIncludesZero(true);

        // customise the StackedXYBarRenderer
        // barwidth (positive) => % to be cut out from the bar width
        // barwidth (negative) => % to be added to the bar width
        float barwidth = 1 - samplingInterval;
        XYBarRenderer renderer = new XYBarRenderer(barwidth);
        renderer.setDrawBarOutline(false);
        renderer.setShadowVisible(false);
        renderer.setBarPainter(new StandardXYBarPainter());

        XYPlot plot = new XYPlot(dataSet, domainAxis, rangeAxis, renderer);
        plot.setOrientation(PlotOrientation.HORIZONTAL);
        plot.setNoDataMessage("No data found !!");

        if (showLegend == 1)
            plot.setFixedLegendItems(getTransparentInvisibleLegend());

        plot.setBackgroundPaint(new Color(255, 255, 255, 0));
        JFreeChart chart = new JFreeChart(title, plot);
        plot.setBackgroundPaint(Color.darkGray);
        chart.setBackgroundPaint(new Color(255, 255, 255, 0));

        chart.getLegend().visible = (showLegend == 0) ? false : true;
        chart.getLegend().setBackgroundPaint(new Color(255, 255, 255, 0));

        chart.getLegend().setPosition(RectangleEdge.RIGHT);
        chart.getLegend().setBorder(0, 0, 0, 0);

        return chart;
    }

    public LegendItemCollection getTransparentInvisibleLegend()
            throws Exception {
        LegendItemCollection collection = new LegendItemCollection();
        try {
            LegendItem litem = new LegendItem("blankblank", new Color(255, 255,
                    255, 0));
            litem.setLinePaint(new Color(255, 255, 255, 0));
            litem.setLabelPaint(new Color(255, 255, 255, 0));
            collection.add(litem);
        } catch (Exception ex) {
            throw ex;
        }
        return collection;
    }

    /**
     * Generating a Multiple Lines Chart base on JFreeChart XYSeriesCollection
     * dataset
     *
     * @param XYSeriesCollection
     *            JFreeChart dataset for plotting line chart
     * @param title
     *            chart title
     * @param arrSeriesColor
     *            an ArrayList storing a list of series' colour
     * @return JFreeChart a line chart in PNG format
     */
    public JFreeChart createLineChart(XYSeriesCollection dataSet, String title,
            ArrayList<Integer> arrSeriesColor, float samplingInterval,
            float start_range, float end_range, int showLegend) {
        logger.debug("createMultipleLinesChart.... ");

        JFreeChart chart = ChartFactory.createXYLineChart(title, "Depth (m)",
                "Meter Averages", dataSet, PlotOrientation.HORIZONTAL, true,
                false, false);

        XYPlot plot = (XYPlot) chart.getPlot();
        plot.setBackgroundPaint(Color.darkGray);
        plot.setNoDataMessage("No data found !!");

        NumberAxis xaxis = (NumberAxis) plot.getDomainAxis();
        NumberAxis yaxis = (NumberAxis) plot.getRangeAxis();

        xaxis.setRange(start_range, end_range);
        yaxis.setAutoRangeIncludesZero(false);
        xaxis.setInverted(true);

        XYLineAndShapeRenderer renderer = (XYLineAndShapeRenderer) plot
                .getRenderer();
        int j = 0;
        for (Iterator<Integer> it1 = arrSeriesColor.iterator(); it1.hasNext();) {
            Integer colorCode = it1.next();
            renderer.setSeriesPaint(j,
                    Utility.BGRColorToJavaColor(colorCode.intValue()));
            j++;
        }

        chart.getLegend().visible = (showLegend == 0) ? false : true;
        chart.getLegend().setBackgroundPaint(new Color(255, 255, 255, 0));
        chart.getLegend().setPosition(RectangleEdge.RIGHT);
        chart.getLegend().setBorder(0, 0, 0, 0);

        return chart;
    }

    /**
     * Generating a Line Chart base on JFreeChart XYSeriesCollection dataset
     *
     * @param XYSeriesCollection
     *            JFreeChart dataset for plotting line chart
     * @param title
     *            chart title
     * @return JFreeChart a line chart in PNG format
     */
    public JFreeChart createLineChart(XYSeriesCollection dataSet, String title,
            float samplingInterval, float start_range, float end_range,
            int showLegend) throws Exception {
        logger.debug("createLineChart.... ");

        JFreeChart chart = ChartFactory.createXYLineChart(title, "Depth (m)",
                "Meter Averages", dataSet, PlotOrientation.HORIZONTAL, true,
                false, false);

        XYPlot plot = (XYPlot) chart.getPlot();
        plot.setBackgroundPaint(Color.darkGray);
        plot.setNoDataMessage("No data found !!");

        if (showLegend == 1)
            plot.setFixedLegendItems(getTransparentInvisibleLegend());

        NumberAxis xaxis = (NumberAxis) plot.getDomainAxis();
        NumberAxis yaxis = (NumberAxis) plot.getRangeAxis();

        xaxis.setRange(start_range, end_range);
        yaxis.setAutoRangeIncludesZero(false);
        xaxis.setInverted(true);

        chart.getLegend().visible = (showLegend == 0) ? false : true;
        chart.getLegend().setBackgroundPaint(new Color(255, 255, 255, 0));
        chart.getLegend().setPosition(RectangleEdge.RIGHT);
        chart.getLegend().setBorder(0, 0, 0, 0);

        return chart;
    }


    /**
     * Validate that the log id (s) exists in LOGS table
     *
     * @param logIdList     a list of log id
     * @return boolean      true if all log id exist in PUBLISHEDLOG table
     *                      else return false
     */
    public boolean validateLogId(String[] logIdList) throws SQLException,
            DataAccessException {
        return nvclDataSvcDao.validateLogId(logIdList);
    }


    /**
     * Validate that domainlog_id for list of log id are similar
     *
     * @param logIdList     a list of log id
     * @return boolean      true if domainlog_id are similar for list of log id
     *                      else return false
     */
    public boolean validateDomainlogId(String[] logIdList) throws SQLException,
            DataAccessException {
        return nvclDataSvcDao.validateDomainlogId(logIdList);
    }


    /**
     * Retrieving scalar details based on a dynamic generated sql
     *
     * @param sql       dynamic generated sql
     * @return List<Map<String, Object>>    dynamic scalar details
     */
    public List<Map<String, Object>> getScalarDetails(String sql, Object[] params) {
        return nvclDataSvcDao.getScalarDetails(sql,params);
    }

    public void streamScalarData(List<LogDetailsVo> logDetailsVoList, Float startdepth, Float enddepth, RowCallbackHandler rch) {
        nvclDataSvcDao.streamScalarData(logDetailsVoList, startdepth, enddepth,rch);
    }



    /**
     * Get the domainlog_id for a specified iamge log based on the
     * image log id
     *
     * @param imagelogId   image log's log_id
     * @return String domainlog_id of the mosaic tray
     */
    public String getImageDomainlogId(String imagelogId) {
        return nvclDataSvcDao.getImageDomainlogId(imagelogId);
    }


    /**
     * Execute the function call in DomainDataDao class.
     * Extract the record set in the returned Map and return
     * the Map to MenuController.
     *
     * @param domainlogId
     *            domainlog_id for a specified image tray
     * @return ArrayList this ArrayList consists of a list of DomainDataVo
     */
    public DomainDataCollectionVo getDomainData(String domainlogId) {


        /**
         * Calling GetDomainDataDao.execute(parameters) will return a Map which
         * consists of only one key (key name is define in the SqlOutParameter
         * in GetDomainDataDao.java) with a value as ArrayList type
         */
        return domainDataDao.execute(domainlogId);

    }

        /**
     * Execute the function call in DomainDataDao class.
     * Extract the record set in the returned Map and return
     * the Map to MenuController.
     *
     * @param domainlogId
     *            domainlog_id for a specified image tray
     * @return ArrayList this ArrayList consists of a list of DomainDataVo
     */
    public DomainDataCollectionVo getDomainDataRelativetoBaseDomain(String domainlogId) {


        return nvclDataSvcDao.getDomainDataRelativetoBaseDomain(domainlogId);

    }


    public SpectralDataCollectionVo getSpectralData(String speclogid, int startsampleno, int endsampleno)
    {
        if (nvclBlobStoreAccessSvc.isConnected) {
            String datasetid = this.getDatasetIdfromLogId(speclogid);
            SpectralDataCollectionVo specdata = nvclBlobStoreAccessSvc.getSpectralData(datasetid, speclogid, startsampleno,endsampleno);
            return specdata;
        }
        else {
            return nvclDataSvcDao.getSpectralData(speclogid, startsampleno,endsampleno);
        }

    }

	public SpectralLogCollectionVo getSpectralLogCollection(String datasetId) {
		if (nvclBlobStoreAccessSvc.isConnected) {
           /* SpectralLogCollectionVo speclogs= nvclDataSvcDao.getSpectralLogsNoSampCount(datasetId);
            for(SpectralLogVo speclog : speclogs.getSpectralLogCollection()){
                speclog.setSampleCount(nvclBlobStoreAccessSvc.getSpectralDataCount(datasetId,speclog.getLogID()));
            }
            return speclogs;*/
            return nvclDataSvcDao.getSpectralLogsDatainBlobStore(datasetId);
        }
        else {
            return nvclDataSvcDao.getSpectralLogs(datasetId);
        }
	}

    public ProfDataCollectionVo getProfData(String proflogid, int startsampleno, int endsampleno)
    {
    	return nvclDataSvcDao.getProfData(proflogid, startsampleno,endsampleno);
    }

	public ProfLogCollectionVo getProfLogCollection(String datasetId) {
		return nvclDataSvcDao.getProfLogs(datasetId);
	}

	public TraySectionsVo getTraySections(String datasetId, int trayindex) {
		return nvclDataSvcDao.getTraySectionRanges(datasetId,trayindex);
	}
	
	public List<ClassDataVo> getClassLogData(String logID, int startSampleNo,int endSampleNo){
		return nvclDataSvcDao.getClassLogData(logID, startSampleNo, endSampleNo);
	}
	
	public List<FloatDataVo> getFloatLogData(String logID, int startSampleNo,int endSampleNo){
		return nvclDataSvcDao.getFloatLogData(logID, startSampleNo, endSampleNo);
	}
	
    /**
     * Execute the function call in LogExtentsDao class.
     * Extract the record set in the returned Map and return
     * the Map to MenuController.
     *
     * @param logID
     *            domainlog_id for a specified image tray
     * @return LogExtentsVo this LogExtentsVo consists of min and max values
     */
    public LogExtentsVo getLogExtents(String logID) {
        return nvclDataSvcDao.getlogExtents(logID);
    }
	
    public List<MaskDataVo> getMaskLogData(String logID, int startSampleNo,int endSampleNo){
		return nvclDataSvcDao.getMaskLogData(logID, startSampleNo,endSampleNo);
    }

    public record SampAndSecNumber(int samplenumber, int sectionnumber) {}
    
    public SampAndSecNumber getSampleNumberfromTrayPictureXY(String imglogId,int SampleNo,int x,int y,int imgwidth,int imgheight)
    {
		
		ImageDataVo imagedata = this.getImageData(imglogId,"", SampleNo,false);

		Metadata metadata;
		try {
			metadata = ImageMetadataReader.readMetadata(new ByteArrayInputStream(imagedata.getImgData()));
		} catch (ImageProcessingException | IOException e1) {
			return null;
		}
		
		JpegDirectory jpegdirectory = metadata.getFirstDirectoryOfType(JpegDirectory.class);
		try {
			int actualwidth = jpegdirectory.getImageWidth();
			int actualheight = jpegdirectory.getImageHeight();
			if (imgwidth==0 || imgheight==0){ 
				imgwidth=actualwidth;
				imgheight=actualheight;
			}
			else if (imgwidth!=actualwidth || imgheight != actualheight){
				x = (int) ((double)x*((double)actualwidth/(double)imgwidth));
				y = (int) ((double)y*((double)actualheight/(double)imgheight));
			}
		} catch (MetadataException e1) {
			return null;
		}
		
		
		Collection<JpegCommentDirectory> jpegcomments = metadata.getDirectoriesOfType(JpegCommentDirectory.class);
		for (Iterator<JpegCommentDirectory> it1 = jpegcomments.iterator(); it1.hasNext();) {
			JpegCommentDirectory comment = it1.next();
			String commenttext = comment.getString(0);
			if (commenttext.isEmpty() || !commenttext.startsWith("IOL34_JID1")) return null;
			commenttext = commenttext.substring(11);
			String[] values = commenttext.trim().split(",");
			int sections = Integer.parseInt(values[1]);
			int borderwidth = Integer.parseInt(values[6])+1;
			int borderheight = Integer.parseInt(values[7]);
			//int totalheight = 0;
			List<Integer> sectionstartheights=new ArrayList<Integer>();
			sectionstartheights.add(borderheight);
			int maxwidth =0;
			for(int i =0;i<sections;i++){
				sectionstartheights.add(sectionstartheights.get(sectionstartheights.size()-1)+Integer.parseInt(values[8+(3*i)]));
				//totalheight+= Integer.parseInt(values[8+(3*i)]);
				maxwidth = Math.max(Integer.parseInt(values[10+(3*i)])-Integer.parseInt(values[9+(3*i)]),maxwidth);
			}
			y=Math.max(y, sectionstartheights.get(0));
			y=Math.min(y,sectionstartheights.get(sectionstartheights.size()-1)-1);
			x=Math.max(x, borderwidth);
			int selectedsection = 0;
			float distanceallongsectionpct = 0.0F;
			for(int i=0;i<sectionstartheights.size()-1;i++){
				if (y >= sectionstartheights.get(i) && y<sectionstartheights.get(i+1))
				{
					selectedsection=i;
					int sectionwidth = Integer.parseInt(values[10+(3*i)])-Integer.parseInt(values[9+(3*i)]);
					distanceallongsectionpct = ((float)x-borderwidth)/sectionwidth;
				}
			}
			TraySectionsVo secs;
			try {
				secs = this.getTraySections(this.getLogDetails(imglogId).getDatasetID(), SampleNo);
			} catch (DataAccessException | SQLException e) {
				return null;
			}
            SectionVo selectedsectionVo = secs.getSections().get(selectedsection);
			int endsampno = selectedsectionVo.getEndsampleno();
			int startsampno = selectedsectionVo.getStartsampleno();
			int sampleno = (int) (((endsampno-startsampno)*distanceallongsectionpct)+startsampno);
			return new SampAndSecNumber(sampleno, selectedsectionVo.getSectionnumber());
		}
		return null;
    }

	public Integer getSampleNumberFromDepth(String datasetid, float depth) {
		DatasetCollectionVo datasetinfo = this.getDatasetCollectionbyDatasetId(datasetid);
		String domid = datasetinfo.getDatasetCollection().get(0).getDomainID();
		List<Integer> samps = nvclDataSvcDao.getNearestSampleNumber(domid, depth);
		if (samps.get(0)!=null) return samps.get(0);
		else return null;
	}
	public AlgorithmCollectionVo getAlgorithmsCollection(){
		return nvclDataSvcDao.getAlgorithmsCollection();
	}
	
	public ClassificationsCollectionVo getClassifications(int algoutputid)
	{
		return nvclDataSvcDao.getClassificationsCollection(algoutputid);
	}

	public ClassificationsCollectionVo getClassifications(String logid) {
		return nvclDataSvcDao.getClassificationsCollection(logid);	
	}

    public String getDatasetIdfromLogId(String logId) {
        return nvclDataSvcDao.getDatasetIdfromLogId(logId);
    }

    public Integer getLastsampleNumber(String domainlogId, Integer startSampleNo, Integer endSampleNo) {
        return nvclDataSvcDao.getLastsampleNumber(domainlogId, startSampleNo, endSampleNo);
    }

    public static BufferedImage rotateImageBy90DegreesAnticlockwise(BufferedImage originalImage) {
        int width = originalImage.getWidth();
        int height = originalImage.getHeight();

        // Create a new image with swapped dimensions
        BufferedImage rotatedImage = new BufferedImage(height, width, originalImage.getType());

        // Create the transformation
        AffineTransform transform = new AffineTransform();
        transform.translate(height / 2.0, width / 2.0);
        transform.rotate(Math.toRadians(-90)); // Rotate anticlockwise
        transform.translate(-width / 2.0, -height / 2.0);

        // Apply the transformation
        AffineTransformOp op = new AffineTransformOp(transform, AffineTransformOp.TYPE_BILINEAR);
        op.filter(originalImage, rotatedImage);

        return rotatedImage;
    }

    public Boolean blobExists(String blobName,String containerName, long modifieddate) {
        return nvclBlobStoreAccessSvc.blobExists(blobName,containerName,modifieddate);
    }

    public void UploadTSGFileBundletoAzureBlobContainer(String blobName,String containerName, File localFile) {
        nvclBlobStoreAccessSvc.UploadTSGFileBundletoAzureBlobContainer(blobName, containerName,  localFile);
    }

    public void touchBlob(String blobName,String containerName) {
        nvclBlobStoreAccessSvc.touchBlob(blobName, containerName);
    }

    public int cleanupoldestblobsinAzureContainer(int daysToKeep, String containerName,String path, int maxGBstoRetain){
        return nvclBlobStoreAccessSvc.cleanupoldestblobsinAzureContainer(daysToKeep, containerName, path, maxGBstoRetain);
    }

    public DomainLogCollectionVo getDomainLogCollectionWithData(String datasetid) {
        
        DomainLogCollectionVo domains = this.getDomainLogCollection(datasetid);

        for(DomainLogVo domain : domains.getdomainLogCollection()) {
			if ( Utility.isAlphanumericOrHyphen(domain.getSubdomainoflogid())) {
				DomainDataCollectionVo domdata = this.getDomainDataRelativetoBaseDomain(domain.getLogID());
				DomainDataCollectionVo domdatadepths = this.getDomainData(domain.getLogID());
				for (DomainDataVo singledomsegment :domdata.getDomainDataCollection()){
					for (DomainDataVo singledomdepthsegment :domdatadepths.getDomainDataCollection()){
						if (singledomsegment.getSampleNo() == singledomdepthsegment.getSampleNo()){
							singledomsegment.setStartValue(singledomdepthsegment.getStartValue());
							singledomsegment.setEndValue(singledomdepthsegment.getEndValue());
							break;
						}
					}
				}
				domain.setdomaindata(domdata.getDomainDataCollection());
			}
		}
        return domains;
    }

    public Optional<AlgorithmOutputVersionVo> findByNameAndVersion(AlgorithmCollectionVo algs, int algorithmId,String outputName,int version) {

        return nvclDataSvcDao.findByNameAndVersion(algs, algorithmId, outputName, version);
    }

    public Optional<AlgorithmOutputVersionVo> findByOutputId(AlgorithmCollectionVo algs, int algorithmOutputId) {
        return nvclDataSvcDao.findByOutputId(algs, algorithmOutputId);
    }

    public InputStream findImageSectioninCache(String datasetid, Integer sectionNo, String cachefolderpath,
            Boolean applycorrection, Boolean horizontal, Boolean highresolution) {

        if (Utility.stringIsBlankorNull(datasetid) || sectionNo == null || sectionNo < 0 ||
                Utility.stringIsBlankorNull(cachefolderpath)) {
            return null;
        }

        File cacheFolder = new File(cachefolderpath);
        File sectionImageCache = new File(cacheFolder, "sectionimagecache");

        if (!sectionImageCache.exists() || !sectionImageCache.isDirectory()) {
            try {
                Files.createDirectories(sectionImageCache.toPath());
            } catch (IOException e) {
                logger.error("Unable to create section image cache directory", e);
                return null;
            }
            return null;
        }

        String orientationSuffix = "";
        if (Boolean.TRUE.equals(horizontal)) {
            orientationSuffix += "_horizontal";
        }
        else {
            orientationSuffix += "_vertical";
        }

        String resolutionSuffix = Boolean.TRUE.equals(highresolution) ? "_highresolution" : "";
        String suffix = (applycorrection == null || applycorrection) ? "corrected" : "uncorrected";
        String fileName = datasetid + sectionNo + suffix + orientationSuffix + resolutionSuffix + ".jpg";

        try {
            File targetFile = new File(sectionImageCache, fileName);
            if (targetFile.exists() && targetFile.isFile()) {
                return Files.newInputStream(targetFile.toPath());
            }

        } catch (IOException ex) {
            logger.error("Unable to open section image cache file", ex);
            return null;

        }
        logger.debug("Image section cache file not found for datasetid=" + datasetid + ", sectionNo=" + sectionNo
                + ", applycorrection=" + applycorrection + ", horizontal=" + horizontal);
        return null;
    }

    public boolean saveImageSectionToCache(String datasetid, Integer sectionNo, String cachefolderpath,
            Boolean applycorrection, Boolean horizontal, Boolean highresolution, byte[] imageData) {

        if (Utility.stringIsBlankorNull(datasetid) || sectionNo == null || sectionNo < 0 ||
                Utility.stringIsBlankorNull(cachefolderpath) || imageData == null || imageData.length == 0) {
            return false;
        }

        File cacheFolder = new File(cachefolderpath);
        File sectionImageCache = new File(cacheFolder, "sectionimagecache");

        try {
            Files.createDirectories(sectionImageCache.toPath());
        } catch (IOException e) {
            logger.error("Unable to create section image cache directory", e);
            return false;
        }

        String orientationSuffix = "";
        if (Boolean.TRUE.equals(horizontal)) {
            orientationSuffix += "_horizontal";
        }
        else {
            orientationSuffix += "_vertical";
        }

        String resolutionSuffix = Boolean.TRUE.equals(highresolution) ? "_highresolution" : "";
        String suffix = (applycorrection == null || applycorrection) ? "corrected" : "uncorrected";
        File targetFile = new File(sectionImageCache, datasetid + sectionNo + suffix + orientationSuffix + resolutionSuffix + ".jpg");

        try {
            Files.write(targetFile.toPath(), imageData);
            return true;
        } catch (IOException ex) {
            logger.error("Unable to write section image cache file", ex);
            return false;
        }
    }

}
