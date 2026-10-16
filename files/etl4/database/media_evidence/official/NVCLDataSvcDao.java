package org.auscope.nvcl.server.dao;

import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.sql.Blob;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import javax.sql.DataSource;


import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.auscope.nvcl.server.vo.AlgorithmCollectionVo;
import org.auscope.nvcl.server.vo.AlgorithmOutputVersionVo;
import org.auscope.nvcl.server.vo.ClassDataVo;
import org.auscope.nvcl.server.vo.ClassificationVo;
import org.auscope.nvcl.server.vo.ClassificationsCollectionVo;
import org.auscope.nvcl.server.vo.DatasetCollectionVo;
import org.auscope.nvcl.server.vo.DatasetVo;
import org.auscope.nvcl.server.vo.DepthRangeVo;
import org.auscope.nvcl.server.vo.DomainDataCollectionVo;
import org.auscope.nvcl.server.vo.DomainDataVo;
import org.auscope.nvcl.server.vo.DomainLogCollectionVo;
import org.auscope.nvcl.server.vo.DomainLogVo;
import org.auscope.nvcl.server.vo.FloatDataVo;
import org.auscope.nvcl.server.vo.ImageDataVo;
import org.auscope.nvcl.server.vo.ImageLogCollectionVo;
import org.auscope.nvcl.server.vo.ImageLogVo;
import org.auscope.nvcl.server.vo.LogCollectionVo;
import org.auscope.nvcl.server.vo.LogDetailsVo;
import org.auscope.nvcl.server.vo.LogExtentsVo;
import org.auscope.nvcl.server.vo.LogVo;
import org.auscope.nvcl.server.vo.MaskDataVo;
import org.auscope.nvcl.server.vo.ProfDataCollectionVo;
import org.auscope.nvcl.server.vo.ProfDataVo;
import org.auscope.nvcl.server.vo.ProfLogCollectionVo;
import org.auscope.nvcl.server.vo.ProfLogVo;
import org.auscope.nvcl.server.vo.SectionVo;
import org.auscope.nvcl.server.vo.SpectralDataCollectionVo;
import org.auscope.nvcl.server.vo.SpectralDataVo;
import org.auscope.nvcl.server.vo.SpectralLogCollectionVo;
import org.auscope.nvcl.server.vo.SpectralLogVo;
import org.auscope.nvcl.server.vo.TraySectionsVo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowCallbackHandler;
import org.springframework.jdbc.core.RowMapper;

/**
 * This Data Access Object (DAO) implement JDBC connection using SpringFramework
 * <code>jdbcTemplate</code>.&nbsp;It gets the <code>dataSource</code>
 * from applicationContext.xml
 * <p>
 *
 * @author Peter Warren
 * @author Florence Tan
 */

public class NVCLDataSvcDao {

	private static final Logger logger = LogManager.getLogger(NVCLDataSvcDao.class);

    private JdbcTemplate jdbcTemplate;

    /**
     * Configure the jdbcTemplate with a dataSource.
     *
     * @param dataSource
     *            database connection as define in applicationContext.xml
     */
    @Autowired
    public void setDataSource(DataSource dataSource) {
        this.jdbcTemplate = new JdbcTemplate(dataSource);
    }

    @Value("${jdbc.dbType}")
    private String dbType;

    private boolean isSqlServer() {
        return "sqlserver".equalsIgnoreCase(dbType);
    }

    /**
     * Getting the list of logs id and name from logs table
     *
     * @param datasetID
     *            dataset id for retrieving a list of log_id and logName
     * @return List a List of LogCollectionVo value object consists of logID and
     *         logName.
     *
     */
    public LogCollectionVo getLogCollection(String datasetId) {
        String sql = "select log_id, logname, ispublic, logtype, ALGORITHMOUTPUT_ID, masklog_id from logs where dataset_id = ? and logtype in (1,2,6) order by log_id";
        RowMapper<LogVo> mapper = new RowMapper<LogVo>() {
            public LogVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                LogVo log = new LogVo();
                log.setLogID(rs.getString("log_id"));
                log.setLogName(rs.getString("logName"));
                log.setIspublic(rs.getBoolean("ispublic"));
                log.setLogType(rs.getInt("logtype"));
                log.setAlgorithmoutID(rs.getInt("ALGORITHMOUTPUT_ID"));
                log.setMaskLogID(rs.getString("masklog_id"));
                return log;
            }
        };
        return new LogCollectionVo((ArrayList<LogVo>) this.jdbcTemplate.query(sql, mapper, datasetId));
    }

    /**
     * Getting the list of log id and log name from LOGS table for Image
     * service. It will return logs with logtype=3 only and order the result
     * in the following order :
     * <ol>
     * <li>1.Mosaic
     * <li>2.Tray Thumbnail Images
     * <li>3.Tray Images
     * <li>4.holeing
     * <li>5.anything else
     * </ol>
     *
     * @param datasetID
     *            dataset id for retrieving a list of log_id and logName
     * @return List a List of LogCollectionMosaicVo value object consists of logID,
     *         logName and samplecount
     */

    public ImageLogCollectionVo getImageLogCollection(String datasetId) {
        String sql = "select log_id, logname, "+ (isSqlServer() ? "dbo.":"" )+"GETDATAPOINTS(logs.DOMAINLOG_ID) as samplecount from logs where dataset_id=? and logtype=3 "
                + "order by case logname when 'Mosaic' then 1 when 'Tray Thumbnail Images' "
                + "then 2 when 'Tray Images' then 3 when 'Imagery' then 4 when 'holeimg' "
                + "then 5 else 6 end, logname";
        RowMapper<ImageLogVo> mapper = new RowMapper<ImageLogVo>() {
            public ImageLogVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                ImageLogVo imglog = new ImageLogVo();
                imglog.setLogID(rs.getString("log_id"));
                imglog.setLogName(rs.getString("logname"));
                imglog.setSampleCount(rs.getInt("samplecount"));
                return imglog;
            }
        };
        return new ImageLogCollectionVo((ArrayList<ImageLogVo>) this.jdbcTemplate.query(sql, mapper, datasetId));
    }


    /**
     * Getting the list of log id and log name from LOGS table for Image
     * service. It will return logs with logtype=3 only and order the result
     * in the following order :
     * <ol>
     * <li>1.Mosaic
     * <li>2.Tray Thumbnail Images
     * <li>3.Tray Images
     * <li>4.holeing
     * <li>5.anything else
     * </ol>
     *
     * @param datasetID
     *            dataset id for retrieving a list of log_id and logName
     * @return List a List of LogCollectionMosaicVo value object consists of logID,
     *         logName and samplecount
     */

     public DomainLogCollectionVo getDomainLogCollection(String datasetId) {
        String sql = "select logs.log_id, logs.logname, "+ (isSqlServer() ? "dbo.":"" )+"GETDATAPOINTS(logs.LOG_ID) as samplecount, domainlogs.ISSUBDOMAINOFLOG_ID from logs inner join DOMAINLOGS on logs.LOG_ID=domainlogs.LOG_ID where logs.dataset_id=? and logs.logtype=0 order by logs.LOG_ID";

        RowMapper<DomainLogVo> mapper = new RowMapper<DomainLogVo>() {
            public DomainLogVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                DomainLogVo domlog = new DomainLogVo();
                domlog.setLogID(rs.getString("log_id"));
                domlog.setLogName(rs.getString("logname"));
                domlog.setSubdomainoflogid(rs.getString("ISSUBDOMAINOFLOG_ID"));
                domlog.setSampleCount(rs.getInt("samplecount"));
                return domlog;
            }
        };
        return new DomainLogCollectionVo((ArrayList<DomainLogVo>) this.jdbcTemplate.query(sql, mapper, datasetId));
    }

    /**
     * Getting the log details (log type and log name) from logs table
     *
     * @param logID
     *            unique log id of logs table
     * @return LogDetailsVo a value object consists of logType and logName.
     *         Value of log type can be either 1 or 2, for log type 1, calling
     *         oracle function <code>GETDOWNSAMPLEDLOGVALUES</code> will return
     *         4 columns and for log type 2, it will return 2 columns. Value of
     *         log name is use as chart tile.
     */
    public LogDetailsVo getLogDetails(String logID) throws SQLException,
            DataAccessException {

        String sql = "select dataset_id,logname, logtype,domainlog_id, algorithmoutput_id from logs where log_id = ?";
        RowMapper<LogDetailsVo> mapper = new RowMapper<LogDetailsVo>() {
            public LogDetailsVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException, DataAccessException {
                logger.debug("in mapRow...");
                LogDetailsVo logDetails = new LogDetailsVo();
                logDetails.setDatasetID(rs.getString("dataset_id"));
                logDetails.setLogName(rs.getString("logname"));
                logDetails.setLogType(rs.getInt("logtype"));
                logDetails.setDomainlogId(rs.getString("domainlog_id"));
                logDetails.setAlgorithmOutputId(rs.getInt("algorithmoutput_id"));
                return logDetails;
            }
        };
        try {
            return this.jdbcTemplate.queryForObject(sql, mapper, logID);
        } catch (EmptyResultDataAccessException e) {
            return null;
        }
    }


    /**
     * Get Log details for a list of log id
     *
     * @param logIDList     list of log id
     * @return List<LogDetailsVo>   return a list of LogDetailsVo
     */
    public List<LogDetailsVo> getLogDetails(String[] logIdList) {
        String sql = "select dataset_id,log_id, logname, logtype, domainlog_id, algorithmoutput_id from logs where log_id in (?";
        for (int i = 1; i < logIdList.length; i++) {
               sql = sql + ",?";
        }
        sql = sql + ")";
        RowMapper<LogDetailsVo> mapper = new RowMapper<LogDetailsVo>() {
            public LogDetailsVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException, DataAccessException {
                LogDetailsVo logDetailsVo = new LogDetailsVo();
                logDetailsVo.setLogId(rs.getString("log_id"));
                logDetailsVo.setLogName(rs.getString("logname"));
                logDetailsVo.setLogType(rs.getInt("logtype"));
                logDetailsVo.setDomainlogId(rs.getString("domainlog_id"));
                logDetailsVo.setDatasetID(rs.getString("dataset_id"));
                logDetailsVo.setAlgorithmOutputId(rs.getInt("algorithmoutput_id"));
                return logDetailsVo;
            }
        };

        return this.jdbcTemplate.query(sql, mapper, (Object[])logIdList);

    }


    /**
     * Retrieves the the domain minimum and maximum depth range.
     *
     * @param logID
     *            log id as primary key for retrieving the domain depth ranges
     */
    public float[] getDomainChartDepthRange(String log_id) throws SQLException,
            DataAccessException {

        String sql = "SELECT MIN(STARTVALUE), MAX(ENDVALUE) FROM DOMAINLOGDATA WHERE LOG_ID=(SELECT DOMAINLOG_ID FROM LOGS WHERE LOG_ID=?)";
        RowMapper<float[]> mapper = new RowMapper<float[]>() {
            public float[] mapRow(ResultSet rs, int rowNum)
                    throws SQLException, DataAccessException {
                float[] retVal = new float[2];
                retVal[0] = rs.getFloat(1);
                retVal[1] = rs.getFloat(2);
                return retVal;
            }
        };

        return this.jdbcTemplate.queryForObject(sql, mapper, log_id);
    }

    
    /**
     * Retrieves the the domain minimum and maximum depth range.
     *
     * @param datasetID
     *            dataset id as primary key for retrieving the domain depth ranges
     */
    public DepthRangeVo getDatasetDepthRange(String dataset_id) throws SQLException,
            DataAccessException {

        String sql = "SELECT MIN(STARTVALUE), MAX(ENDVALUE) FROM DOMAINLOGDATA WHERE LOG_ID=(SELECT DOMAIN_ID FROM DATASETS WHERE DATASET_ID=?)";
        RowMapper<DepthRangeVo> mapper = new RowMapper<DepthRangeVo>() {
            public DepthRangeVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException, DataAccessException {
                DepthRangeVo retVal = new DepthRangeVo();
                retVal.setstart(rs.getFloat(1));
                retVal.setend(rs.getFloat(2));
                return retVal;
            }
        };

        return this.jdbcTemplate.queryForObject(sql, mapper, dataset_id);
    }

    /**
     * Getting the list of dataset id and dataset name from Datasets table
     *
     * @param holeIdentifier
     *            holeIdentifier is same as the borehole id from the borehole
     *            header info return from the geoserver wfs
     * @return List a List of DatasetCollectionVo value object consists of
     *         datasetID and datasetName.
     *
     */

    public DatasetCollectionVo getDatasetCollection(String holeIdentifier) {
    	String sql;

        RowMapper<DatasetVo> mapper = new RowMapper<DatasetVo>() {
            public DatasetVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                DatasetVo dataset = new DatasetVo();
                dataset.setDatasetID(rs.getString("dataset_id"));
                dataset.setDatasetName(rs.getString("datasetname"));
                dataset.setDescription(rs.getString("dsdescription"));
                dataset.setBoreholeURI(rs.getString("holedatasourcename")+rs.getString("holeidentifier"));
                dataset.setTrayID(rs.getString("traylog_id"));
                dataset.setSectionID(rs.getString("sectionlog_id"));
                dataset.setDomainID(rs.getString("domain_id"));
                dataset.setModifiedDate(rs.getDate("modifieddate"));
                dataset.setCreatedDate(rs.getDate("createddate"));
                return dataset;
            }
        };
    	if(holeIdentifier.equalsIgnoreCase("all")){ 
    		sql= "select dataset_id, datasetname, holedatasourcename, holeidentifier, dsdescription, traylog_id, sectionlog_id, domain_id, modifieddate, createddate from publisheddatasets";
    		return new DatasetCollectionVo( (ArrayList<DatasetVo>) this.jdbcTemplate.query(sql, mapper));

    	}
    	else {
    		sql= "select dataset_id, datasetname,holedatasourcename, holeidentifier, dsdescription, traylog_id, sectionlog_id, domain_id, modifieddate, createddate from publisheddatasets where holeidentifier= ?";
    		return new DatasetCollectionVo( (ArrayList<DatasetVo>) this.jdbcTemplate.query(sql, mapper, holeIdentifier));	
    	}
    }

    /**
     * Getting the list of dataset id and dataset name from Datasets table by datasetID
     *
     * @param datasetID
     *            datasetID is the identifier of a nvcl dataset.
     * @return List a List of DatasetCollectionVo value object consists of
     *         datasetID and datasetName.
     *
     */

    public DatasetCollectionVo getDatasetCollectionbyDatasetId(String datasetId) {
    	String sql;

        RowMapper<DatasetVo> mapper = new RowMapper<DatasetVo>() {
            public DatasetVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                DatasetVo dataset = new DatasetVo();
                dataset.setDatasetID(rs.getString("dataset_id"));
                dataset.setDatasetName(rs.getString("datasetname"));
                dataset.setDescription(rs.getString("dsdescription"));
                dataset.setBoreholeURI(rs.getString("holedatasourcename")+rs.getString("holeidentifier"));
                dataset.setTrayID(rs.getString("traylog_id"));
                dataset.setSectionID(rs.getString("sectionlog_id"));
                dataset.setDomainID(rs.getString("domain_id"));
                dataset.setModifiedDate(rs.getDate("modifieddate"));
                dataset.setCreatedDate(rs.getDate("createddate"));
                return dataset;
            }
        };
        sql= "select dataset_id, datasetname,holedatasourcename, holeidentifier, dsdescription, traylog_id, sectionlog_id, domain_id, modifieddate, createddate from publisheddatasets where dataset_ID= ?";
    	return new DatasetCollectionVo( (ArrayList<DatasetVo>) this.jdbcTemplate.query(sql, mapper, datasetId));	

    }

        /**
     * Getting the list of dataset id and dataset name from Datasets table by datasetID
     *
     * @param datasetID
     *            datasetID is the identifier of a nvcl dataset.
     * @return List a List of DatasetCollectionVo value object consists of
     *         datasetID and datasetName.
     *
     */

     public DatasetCollectionVo getDatasetCollectionbyDatasetName(String datasetName) {
    	String sql;

        RowMapper<DatasetVo> mapper = new RowMapper<DatasetVo>() {
            public DatasetVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                DatasetVo dataset = new DatasetVo();
                dataset.setDatasetID(rs.getString("dataset_id"));
                dataset.setDatasetName(rs.getString("datasetname"));
                dataset.setDescription(rs.getString("dsdescription"));
                dataset.setBoreholeURI(rs.getString("holedatasourcename")+rs.getString("holeidentifier"));
                dataset.setTrayID(rs.getString("traylog_id"));
                dataset.setSectionID(rs.getString("sectionlog_id"));
                dataset.setDomainID(rs.getString("domain_id"));
                dataset.setModifiedDate(rs.getDate("modifieddate"));
                dataset.setCreatedDate(rs.getDate("createddate"));
                return dataset;
            }
        };
        sql= "select dataset_id, datasetname,holedatasourcename, holeidentifier, dsdescription, traylog_id, sectionlog_id, domain_id, modifieddate, createddate from publisheddatasets where datasetname= ?";
    	return new DatasetCollectionVo( (ArrayList<DatasetVo>) this.jdbcTemplate.query(sql, mapper, datasetName));	

    }

        /**
     * Getting the hole URI from Datasets table by datasetID
     *
     * @param datasetID
     *            datasetID is the identifier of a nvcl dataset.
     * @return List a List of DatasetCollectionVo value object consists of
     *         datasetID and datasetName.
     *
     */

    public String getBoreholeHoleURIbyDatasetId(String datasetId) {
    	String sql;

        RowMapper<String> mapper = new RowMapper<String>() {
            public String mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                String boreholeURI = rs.getString("HOLEDATASOURCENAME")+rs.getString("HOLEIDENTIFIER");
                return boreholeURI;
            }
        };
        sql= "select HOLEDATASOURCENAME, HOLEIDENTIFIER  from publisheddatasets where dataset_ID= ?";
    	return this.jdbcTemplate.query(sql, mapper, datasetId).get(0);	

    }
    
    /**
     * Getting the sample number from IMAGELOGDATA table
     *
     * @param logID
     *            log id as primary key for retrieving the tray number
     *            (samplenumber)
     * @param startSampleNo
     *            starting number for the range of sample number to retrieve
     * @param endSampleNo
     *            ending number for the range of sample number to retrieve
     * @return ImageLogsVo a value object that hold a list of sample number
     *         (borehole tray id)
     */
    public List<ImageDataVo> getSampleNo(String logID, int startSampleNo,
            int endSampleNo) {
        String sql = "select samplenumber from publishedimagelogdata where log_id = ? "
                + "and samplenumber between ? and ? " + "order by samplenumber";
        RowMapper<ImageDataVo> mapper = new RowMapper<ImageDataVo>() {
            public ImageDataVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                ImageDataVo imageDataVo = new ImageDataVo();
                imageDataVo.setSampleNo(rs.getInt("samplenumber"));
                return imageDataVo;
            }
        };
        logger.debug("getSampleNo end...");
        return this.jdbcTemplate.query(sql, mapper, logID, startSampleNo,
                endSampleNo);
    }

    /**
     * Getting the tray image from IMAGELOGS table
     *
     * @param logID
     *            log id as primary key for retrieving the tray thumbnail image
     * @param sampleNo
     *            sample number as second primary key for retrieving the tray
     *            thumbnail image, one log id come with thousands of sample tray
     *            images
     * @return ImageLogsVo a value object consists the borehole tray thumbnail
     *         image store as BLOB data type in oracle db
     */
    public ImageDataVo getImgData(String logID, int sampleNo) {
        String sql = "select publishedimagelogdata.imagedata, imagelogs.imghistogram, imagelogs.imgclippercent from publishedimagelogdata inner join imagelogs on publishedimagelogdata.log_id = imagelogs.log_id where imagelogs.log_id = ? and publishedimagelogdata.samplenumber = ?";
        RowMapper<ImageDataVo> mapper = new RowMapper<ImageDataVo>() {
            public ImageDataVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                ImageDataVo imageDataVo = new ImageDataVo();
                Blob imgBlob = rs.getBlob("imagedata");
                if (!rs.wasNull() && imgBlob!=null) imageDataVo.setImgData(imgBlob.getBytes(1, (int)imgBlob.length()));
                Blob LUTBlob = rs.getBlob("imghistogram");
                if (!rs.wasNull() && LUTBlob!=null) imageDataVo.setImgHistogramLUT(LUTBlob.getBytes(1, (int)LUTBlob.length()));
                return imageDataVo;
            }
        };
        logger.debug("getImgData end...");
        return this.jdbcTemplate.queryForObject(sql, mapper, logID,
                sampleNo);
    }

    /**
     * Getting the image histogram from IMAGELOGS table
     *
     * @param logID
     *            log id as primary key for retrieving the image log
     * @return ImageLogsVo a value object consists the borehole tray thumbnail
     *         image store as BLOB data type in oracle db
     */
    public byte[] getImgHistogramData(String logID) {
        String sql = "select imghistogram from imagelogs where log_id = ?";
        return (byte[]) this.jdbcTemplate.queryForObject(sql,byte[].class,logID );
    }


    /**
     * Validate if log id(s) exist in LOGS table
     *
     * @param logIDList    list of log id
     * @return verifyResult boolean value - return true if all log ids exist in the table
     *                      else return false
     */
    public boolean validateLogId(String[] logIdList) {
        String sql = String.format("select count(*) from logs where log_id in (%s)",String.join(",", Collections.nCopies(logIdList.length, "?")));

        int logCount = this.jdbcTemplate.queryForObject(sql,Integer.class,(Object[])logIdList);
        if (logCount== logIdList.length)
            return true;
        else
            return false;

    }


    /**
     * Validate if domainlog_id are the same for a list of log id
     *
     * @param logIDList    list of log id
     * @return verifyResult boolean value - return true if all log ids exist in the table
     *                      else return false
     */
    public boolean validateDomainlogId(String[] logIdList) {
        String sql = String.format("select count(distinct domainlog_id) from logs where log_id in (%s)",String.join(",", Collections.nCopies(logIdList.length, "?")));

        int logCount = this.jdbcTemplate.queryForObject(sql,Integer.class,(Object[])logIdList);
        if (logCount == 1)
            return true;
        else
            return false;

    }


    /**
     * Retrieve scalar values based on the dynamic sql passed in
     *
     * @param sql   dynamic sql generated from menu controller
     * @return List<Map<String, Object>>
     *              Scalar values for a list of scalar(s)
     */
    public List<Map<String, Object>> getScalarDetails(String sql,Object[] params) {

        List<Map<String, Object>> scalarMaps = jdbcTemplate.queryForList(sql,params);
        return scalarMaps;
    }

    public void streamScalarData(List<LogDetailsVo> logDetailsVoList, Float startdepth, Float enddepth,RowCallbackHandler handler) {

        List<Object> params = new ArrayList<>();

        StringBuilder select = new StringBuilder(
            "SELECT DOMAINLOGDATA.STARTVALUE AS \"StartDepth\", DOMAINLOGDATA.ENDVALUE AS \"EndDepth\""
        );
        StringBuilder from = new StringBuilder(" FROM DOMAINLOGDATA ");
        StringBuilder where = new StringBuilder(" WHERE DOMAINLOGDATA.LOG_ID = ? ");

        List<String> strKeysArr = new ArrayList<>();
        strKeysArr.add("StartDepth");
        strKeysArr.add("EndDepth");

        String domainlogId = null;
        int i = 0;

        AlgorithmCollectionVo algs = this.getAlgorithmsCollection();

        for (LogDetailsVo logDetailsVo : logDetailsVoList) {

            i++;

            String logId = logDetailsVo.getLogId();
            String logName = logDetailsVo.getLogName();
            int logType = logDetailsVo.getLogType();

            if (i == 1) {
                domainlogId = logDetailsVo.getDomainlogId();
                params.add(domainlogId);
            }

            String safeName = logName.replaceAll("[^a-zA-Z0-9]", "_");
            Optional<AlgorithmOutputVersionVo> algorithmOutput = findByOutputId(algs, logDetailsVo.getAlgorithmOutputId());

            if (algorithmOutput.isPresent() && algorithmOutput.get().getVersion() != 0) {
                safeName = safeName + "_v" + algorithmOutput.get().getVersion();
            }
            strKeysArr.add(safeName);

            if (logType != 1 && logType != 2 && logType != 5 && logType != 6) {
                throw new RuntimeException("Invalid logType: " + logType);
            }

            switch (logType) {

                case 1:
                    select.append(", COALESCE(classspec").append(i)
                        .append(".CLASSTEXT, class").append(i)
                        .append(".CLASSTEXT) AS \"").append(safeName).append("\"");

                    from.append(" INNER JOIN CLASSLOGDATA result").append(i)
                        .append(" ON result").append(i).append(".SAMPLENUMBER = DOMAINLOGDATA.SAMPLENUMBER")

                        .append(" LEFT JOIN CLASSSPECIFICCLASSIFICATIONS classspec").append(i)
                        .append(" ON result").append(i).append(".CLASSLOGVALUE = classspec").append(i).append(".INTINDEX")
                        .append(" AND classspec").append(i).append(".LOG_ID = result").append(i).append(".LOG_ID")

                        .append(" LEFT JOIN LOGS log").append(i)
                        .append(" ON result").append(i).append(".LOG_ID = log").append(i).append(".LOG_ID")

                        .append(" LEFT JOIN CLASSIFICATIONS class").append(i)
                        .append(" ON result").append(i).append(".CLASSLOGVALUE = class").append(i).append(".INTINDEX")
                        .append(" AND class").append(i).append(".ALGORITHMOUTPUT_ID = log").append(i).append(".ALGORITHMOUTPUT_ID");

                    break;

                case 2:
                    select.append(", result").append(i).append(".DECIMALVALUE AS \"").append(safeName).append("\"");

                    from.append(" INNER JOIN DECIMALLOGDATA result").append(i)
                        .append(" ON result").append(i).append(".SAMPLENUMBER = DOMAINLOGDATA.SAMPLENUMBER");

                    break;

                case 5:
                    select.append(", result").append(i).append(".SPECTRALVALUES AS \"").append(safeName).append("\"");

                    from.append(" INNER JOIN SPECTRALLOGDATA result").append(i)
                        .append(" ON result").append(i).append(".SAMPLENUMBER = DOMAINLOGDATA.SAMPLENUMBER");

                    break;

                case 6:
                    select.append(", result").append(i).append(".MASKVALUE AS \"").append(safeName).append("\"");

                    from.append(" INNER JOIN MASKLOGDATA result").append(i)
                        .append(" ON result").append(i).append(".SAMPLENUMBER = DOMAINLOGDATA.SAMPLENUMBER");

                    break;
            }

            // ✅ IMPORTANT: keep filter in WHERE (not JOIN)
            where.append(" AND result").append(i).append(".LOG_ID = ?");
            params.add(logId);
        }

        // depth filters (correctly typed)
        if (startdepth != null && enddepth != null) {
            where.append(" AND DOMAINLOGDATA.STARTVALUE > ?");
            where.append(" AND DOMAINLOGDATA.ENDVALUE < ?");

            params.add(startdepth);
            params.add(enddepth);
        }

        // final SQL
        String sql = select.toString() + from.toString() + where.toString() + " ORDER BY DOMAINLOGDATA.SAMPLENUMBER";
        if (isSqlServer()) {
            sql += " OPTION (OPTIMIZE FOR UNKNOWN)";
        }
        final String finalSql = sql;
        
        jdbcTemplate.query(con -> {
            PreparedStatement ps = con.prepareStatement(finalSql);

            for (int j = 0; j < params.size(); j++) {
                Object param = params.get(j);
                if (param instanceof String) {
                    ps.setString(j+ 1, (String) param);
                } else if (param instanceof Integer) {
                    ps.setInt(j + 1, (Integer) param);
                } else if (param instanceof Long) {
                    ps.setLong(j + 1, (Long) param);
                } else if (param instanceof Float) {
                    ps.setFloat(j + 1, (Float) param);
                } else if (param instanceof Double) {
                    ps.setDouble(j + 1, (Double) param);
                } else if (param instanceof Boolean) {
                    ps.setBoolean(j + 1, (Boolean) param);
                } else {
                    ps.setObject(j + 1, param);
                }
            }

            return ps;

        }, handler);
    }


    /**
     * Getting domainlog_id for a specific Image log id
     *
     * @param logId     Image log id - from getLogCollection service
     * @return String   domainlog_id
     */

    public String getImageDomainlogId(String imagelogId) {
        String sql = "select domainlog_id from logs where log_id=?";

        try {
            return jdbcTemplate.queryForObject(sql, String.class, imagelogId);
        } catch (EmptyResultDataAccessException e) {
            return null;
        }
    }


    

    /**
     * Getting the spectral data from SPECTRALLOGDATA table
     *
     * @param speclogID
     *            log identifier of the spectral log requested
     * @param startsampleNo
     *            start sample number of data requested
     * @param endsampleNo
     *            end sample number of data requested
     * @return SpectralDataCollectionVo a value object consisting of the spectral data in bytes
     */
    
	public SpectralDataCollectionVo getSpectralData(String speclogid,
			int startsampleno, int endsampleno) {
		String sql = "select samplenumber,spectralvalues from spectrallogdata where log_id = ? and samplenumber between ? and ? order by samplenumber";
		RowMapper<SpectralDataVo> mapper = new RowMapper<SpectralDataVo>(){
			public SpectralDataVo mapRow(ResultSet rs, int rowNum)
			throws SQLException {
				SpectralDataVo spectralDataVo = new SpectralDataVo();
                spectralDataVo.setSampleNo(rs.getInt("samplenumber"));
                Blob specdataBlob = rs.getBlob("spectralvalues");
				if (!rs.wasNull() && specdataBlob !=null) spectralDataVo.setSpectraldata(specdataBlob.getBytes(1,(int)specdataBlob.length()));
				return spectralDataVo;
			}
		};
		return new SpectralDataCollectionVo((ArrayList<SpectralDataVo>)this.jdbcTemplate.query(sql, mapper, speclogid,startsampleno,endsampleno));
	}

	public SpectralLogCollectionVo getSpectralLogs(String datasetId) {
		String sql = "select logs.LOG_ID,logs.LOGNAME, "+ (isSqlServer() ? "dbo.":"" )+"GETDATAPOINTS(logs.LOG_ID) as samplecount, logs.customscript, spectrallogs.SPECTRALSAMPLINGPOINTS,spectrallogs.SPECTRALUNITS,spectrallogs.fwhm,spectrallogs.tirq from logs inner join spectrallogs on logs.log_id=spectrallogs.log_id where logs.dataset_id=? and logtype =5 order by spectrallogs.LAYERORDER";
		RowMapper<SpectralLogVo> mapper = new RowMapper<SpectralLogVo>(){
			public SpectralLogVo mapRow(ResultSet rs, int rowNum)
			throws SQLException {
				SpectralLogVo spectralLog = new SpectralLogVo();
				spectralLog.setLogID(rs.getString("LOG_ID"));
				spectralLog.setLogName(rs.getString("LOGNAME"));
				spectralLog.setSampleCount(rs.getInt("samplecount"));
				spectralLog.setScript(rs.getString("customscript"));
				
				Blob specblob = rs.getBlob("SPECTRALSAMPLINGPOINTS");
				if (!rs.wasNull() && specblob!=null){
					FloatBuffer floatbuf = ByteBuffer.wrap( specblob.getBytes(1,(int)specblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] floatArray = new float[floatbuf.limit()];
					floatbuf.get(floatArray);
					spectralLog.setWavelengths(floatArray);
				}
				Blob fwhmblob = rs.getBlob("fwhm");
				if (!rs.wasNull() && fwhmblob!=null){
					FloatBuffer fwhmfloatbuf = ByteBuffer.wrap( fwhmblob.getBytes(1,(int)fwhmblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] fwhmfloatArray = new float[fwhmfloatbuf.limit()];
					fwhmfloatbuf.get(fwhmfloatArray);
					spectralLog.setFwhm(fwhmfloatArray);
				}
				Blob tirqblob = rs.getBlob("tirq");
				if (!rs.wasNull() && tirqblob!=null){
					FloatBuffer tirqfloatbuf = ByteBuffer.wrap( tirqblob.getBytes(1,(int)tirqblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] tirqfloatArray = new float[tirqfloatbuf.limit()];
					tirqfloatbuf.get(tirqfloatArray);
					spectralLog.setTirq(tirqfloatArray);
				}
				
				spectralLog.setWavelengthUnits(rs.getString("SPECTRALUNITS"));
				return spectralLog;
			}
		};
		return new SpectralLogCollectionVo((ArrayList<SpectralLogVo>)this.jdbcTemplate.query(sql, mapper, datasetId));
	
	}

    public SpectralLogCollectionVo getSpectralLogsDatainBlobStore(String datasetId) {
		String sql = "select logs.LOG_ID,logs.LOGNAME, "+ (isSqlServer() ? "dbo.":"" )+"GETDATAPOINTS(logs.DOMAINLOG_ID) as samplecount, logs.customscript, spectrallogs.SPECTRALSAMPLINGPOINTS,spectrallogs.SPECTRALUNITS,spectrallogs.fwhm,spectrallogs.tirq, LOGDEPENDENCIES.DEPENDSON from logs inner join spectrallogs on logs.log_id=spectrallogs.log_id left join LOGDEPENDENCIES on logs.log_id=LOGDEPENDENCIES.LOG_ID where logs.dataset_id=? and logtype =5 order by spectrallogs.LAYERORDER";
        RowMapper<SpectralLogVo> mapper = new RowMapper<SpectralLogVo>(){
			public SpectralLogVo mapRow(ResultSet rs, int rowNum)
			throws SQLException {
				SpectralLogVo spectralLog = new SpectralLogVo();
				spectralLog.setLogID(rs.getString("LOG_ID"));
				spectralLog.setLogName(rs.getString("LOGNAME"));
                if (rs.getObject("DEPENDSON") == null) {
                    spectralLog.setSampleCount(rs.getInt("samplecount"));
                }
                else {
                    spectralLog.setSampleCount(0); // only base spectral layers have sample count, derived layers will be 0. base layers dont depend on any other layer.
                }

				spectralLog.setScript(rs.getString("customscript"));
				
				Blob specblob = rs.getBlob("SPECTRALSAMPLINGPOINTS");
				if (!rs.wasNull() && specblob!=null){
					FloatBuffer floatbuf = ByteBuffer.wrap( specblob.getBytes(1,(int)specblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] floatArray = new float[floatbuf.limit()];
					floatbuf.get(floatArray);
					spectralLog.setWavelengths(floatArray);
				}
				Blob fwhmblob = rs.getBlob("fwhm");
				if (!rs.wasNull() && fwhmblob!=null){
					FloatBuffer fwhmfloatbuf = ByteBuffer.wrap( fwhmblob.getBytes(1,(int)fwhmblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] fwhmfloatArray = new float[fwhmfloatbuf.limit()];
					fwhmfloatbuf.get(fwhmfloatArray);
					spectralLog.setFwhm(fwhmfloatArray);
				}
				Blob tirqblob = rs.getBlob("tirq");
				if (!rs.wasNull() && tirqblob!=null){
					FloatBuffer tirqfloatbuf = ByteBuffer.wrap( tirqblob.getBytes(1,(int)tirqblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] tirqfloatArray = new float[tirqfloatbuf.limit()];
					tirqfloatbuf.get(tirqfloatArray);
					spectralLog.setTirq(tirqfloatArray);
				}
				
				spectralLog.setWavelengthUnits(rs.getString("SPECTRALUNITS"));
				return spectralLog;
			}
		};
		return new SpectralLogCollectionVo((ArrayList<SpectralLogVo>)this.jdbcTemplate.query(sql, mapper, datasetId));
	
	}


    public SpectralLogCollectionVo getSpectralLogsNoSampCount(String datasetId) {
		String sql = "select logs.LOG_ID,logs.LOGNAME, logs.customscript, spectrallogs.SPECTRALSAMPLINGPOINTS,spectrallogs.SPECTRALUNITS,spectrallogs.fwhm,spectrallogs.tirq from logs inner join spectrallogs on logs.log_id=spectrallogs.log_id where logs.dataset_id=? and logtype =5 order by spectrallogs.LAYERORDER";
		RowMapper<SpectralLogVo> mapper = new RowMapper<SpectralLogVo>(){
			public SpectralLogVo mapRow(ResultSet rs, int rowNum)
			throws SQLException {
				SpectralLogVo spectralLog = new SpectralLogVo();
				spectralLog.setLogID(rs.getString("LOG_ID"));
				spectralLog.setLogName(rs.getString("LOGNAME"));
				spectralLog.setScript(rs.getString("customscript"));
				
				Blob specblob = rs.getBlob("SPECTRALSAMPLINGPOINTS");
				if (!rs.wasNull() && specblob!=null){
					FloatBuffer floatbuf = ByteBuffer.wrap( specblob.getBytes(1,(int)specblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] floatArray = new float[floatbuf.limit()];
					floatbuf.get(floatArray);
					spectralLog.setWavelengths(floatArray);
				}
				Blob fwhmblob = rs.getBlob("fwhm");
				if (!rs.wasNull() && fwhmblob!=null){
					FloatBuffer fwhmfloatbuf = ByteBuffer.wrap( fwhmblob.getBytes(1,(int)fwhmblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] fwhmfloatArray = new float[fwhmfloatbuf.limit()];
					fwhmfloatbuf.get(fwhmfloatArray);
					spectralLog.setFwhm(fwhmfloatArray);
				}
				Blob tirqblob = rs.getBlob("tirq");
				if (!rs.wasNull() && tirqblob!=null){
					FloatBuffer tirqfloatbuf = ByteBuffer.wrap( tirqblob.getBytes(1,(int)tirqblob.length())).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
					float[] tirqfloatArray = new float[tirqfloatbuf.limit()];
					tirqfloatbuf.get(tirqfloatArray);
					spectralLog.setTirq(tirqfloatArray);
				}
				
				spectralLog.setWavelengthUnits(rs.getString("SPECTRALUNITS"));
				return spectralLog;
			}
		};
		return new SpectralLogCollectionVo((ArrayList<SpectralLogVo>)this.jdbcTemplate.query(sql, mapper, datasetId));
	
	}


	public ProfLogCollectionVo getProfLogs(String datasetId) {
		String sql = "select logs.LOG_ID, logs.LOGNAME, "+ (isSqlServer() ? "dbo.":"" )+"GETDATAPOINTS(logs.LOG_ID) as samplecount, PROFLOGS.FLOATSPERSAMPLE, PROFLOGS.MINVAL, PROFLOGS.MAXVAL from logs inner join PROFLOGS on logs.log_id=PROFLOGS.LOG_ID where logs.dataset_id=? and logtype =4";
		RowMapper<ProfLogVo> mapper = new RowMapper<ProfLogVo>(){
			public ProfLogVo mapRow(ResultSet rs, int rowNum)
			throws SQLException {
				ProfLogVo profLog = new ProfLogVo();
				profLog.setLogID(rs.getString("LOG_ID"));
				profLog.setLogName(rs.getString("LOGNAME"));
				profLog.setSampleCount(rs.getInt("samplecount"));
				profLog.setFloatsPerSample(rs.getInt("FLOATSPERSAMPLE"));
				profLog.setMinVal(rs.getFloat("MINVAL"));
				profLog.setMaxVal(rs.getFloat("MAXVAL"));

				return profLog;
			}
		};
		return new ProfLogCollectionVo((ArrayList<ProfLogVo>)this.jdbcTemplate.query(sql, mapper, datasetId));
	
	}
	
	public ProfDataCollectionVo getProfData(String proflogid,int startsampleno, int endsampleno) {
		String sql = "select samplenumber,PROFILOMETERVALUES from proflogdata where log_id = ? and samplenumber between ? and ? order by samplenumber";
		RowMapper<ProfDataVo> mapper = new RowMapper<ProfDataVo>(){
			public ProfDataVo mapRow(ResultSet rs, int rowNum)
			throws SQLException {
				ProfDataVo profDataVo = new ProfDataVo();
                profDataVo.setSampleNo(rs.getInt("samplenumber"));
                Blob profdataBlob = rs.getBlob("PROFILOMETERVALUES");
				if (!rs.wasNull() && profdataBlob!=null) profDataVo.setProfdata(profdataBlob.getBytes(1,(int)profdataBlob.length()));
				return profDataVo;
			}
		};
		return new ProfDataCollectionVo((ArrayList<ProfDataVo>)this.jdbcTemplate.query(sql, mapper, proflogid,startsampleno,endsampleno));
	}
	
	 /**
     * Gets tray section ranges.
     *
     * @param datasetID
     *            dataset id of the dataset.
     * @param trayindex
     * 			tray index.
     * @return A List of TrayVo value object sections and their ranges.
     */

    public TraySectionsVo getTraySectionRanges(String datasetId, int trayindex) {
        String sql = "SELECT sec.samplenumber, sec.startvalue,sec.ENDVALUE FROM Domainlogdata sec left outer join domainlogdata on sec.STARTVALUE >= domainlogdata.startvalue AND sec.ENDVALUE<= domainlogdata.ENDVALUE inner join domainlogs on Domainlogdata.LOG_ID = domainlogs.log_id inner join logs on domainlogs.log_id=logs.DOMAINLOG_ID inner join logs logs1 on sec.log_id=logs1.DOMAINLOG_ID inner join datasets on datasets.DATASET_ID=logs.dataset_id where logs.DATASET_ID=? AND logs.LOG_ID=datasets.TRAYLOG_ID AND domainlogdata.samplenumber=? AND logs1.LOG_ID = datasets.SECTIONLOG_ID order by sec.samplenumber";
        RowMapper<SectionVo> mapper = new RowMapper<SectionVo>() {
            public SectionVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
            	SectionVo section = new SectionVo();
            	section.setSectionnumber(rs.getInt("samplenumber"));
            	section.setStartsampleno(rs.getInt("startvalue"));
            	section.setEndsampleno(rs.getInt("endvalue"));
                return section;
            }
        };
        return new TraySectionsVo((ArrayList<SectionVo>) this.jdbcTemplate.query(sql, mapper, datasetId,trayindex));
    }

    
    /**
     * Gets class log data
     *
     * @param logID
     *            log identifier
     * @param startSampleNo
     *            starting sample number for the range of data to retrieve
     * @param endSampleNo
     *            ending sample number for the range of data to retrieve
     * @return ClassDataVo a value object that hold a class log type data
     */
    public List<ClassDataVo> getClassLogData(String logID, int startSampleNo,int endSampleNo) {
        String sql = "select DOMAINLOGDATA.samplenumber,DOMAINLOGDATA.STARTVALUE, coalesce (CLASSSPECIFICCLASSIFICATIONS.CLASSTEXT, CLASSIFICATIONS.CLASSTEXT) as classtext,coalesce (CLASSSPECIFICCLASSIFICATIONS.COLOUR, CLASSIFICATIONS.COLOUR) as colour from domainlogdata inner join CLASSLOGDATA on CLASSLOGDATA.SAMPLENUMBER=DOMAINLOGDATA.SAMPLENUMBER LEFT OUTER JOIN CLASSSPECIFICCLASSIFICATIONS on CLASSLOGDATA.CLASSLOGVALUE = CLASSSPECIFICCLASSIFICATIONS.INTINDEX and CLASSSPECIFICCLASSIFICATIONS.LOG_ID=CLASSLOGDATA.LOG_ID LEFT OUTER JOIN LOGS on CLASSLOGDATA.log_id=LOGS.log_id LEFT OUTER JOIN CLASSIFICATIONS ON CLASSLOGDATA.CLASSLOGVALUE = CLASSIFICATIONS.INTINDEX and CLASSIFICATIONS.ALGORITHMOUTPUT_ID=LOGS.algorithmoutput_id WHERE CLASSLOGDATA.LOG_ID=? and Domainlogdata.log_id = logs.domainlog_id AND Domainlogdata.samplenumber between ? and ? order by Domainlogdata.samplenumber";
        RowMapper<ClassDataVo> mapper = new RowMapper<ClassDataVo>() {
            public ClassDataVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
            	ClassDataVo classData = new ClassDataVo();
            	classData.setSamplenumber(rs.getInt("samplenumber"));
            	classData.setDepth(rs.getFloat("STARTVALUE"));
            	classData.setClassText(rs.getString("classtext"));
            	classData.setColour(rs.getInt("colour"));
                return classData;
            }
        };
        return this.jdbcTemplate.query(sql, mapper, logID, startSampleNo,endSampleNo);
    }
    
    /**
     * Gets float log data
     *
     * @param logID
     *            log identifier
     * @param startSampleNo
     *            starting sample number for the range of data to retrieve
     * @param endSampleNo
     *            ending sample number for the range of data to retrieve
     * @return List<FloatDataVo> a list of value objects that hold a float log type data
     */
    public List<FloatDataVo> getFloatLogData(String logID, int startSampleNo,int endSampleNo) {
        String sql = "select DOMAINLOGDATA.samplenumber,DOMAINLOGDATA.STARTVALUE, DECIMALLOGDATA.DECIMALVALUE from domainlogdata inner join DECIMALLOGDATA on DECIMALLOGDATA.SAMPLENUMBER=DOMAINLOGDATA.SAMPLENUMBER INNER JOIN LOGS on DECIMALLOGDATA.log_id=LOGS.log_id WHERE DECIMALLOGDATA.LOG_ID=? and Domainlogdata.log_id = logs.domainlog_id AND Domainlogdata.samplenumber between ? and ? order by Domainlogdata.samplenumber";
        RowMapper<FloatDataVo> mapper = new RowMapper<FloatDataVo>() {
            public FloatDataVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
            	FloatDataVo floatData = new FloatDataVo();
            	floatData.setSamplenumber(rs.getInt("samplenumber"));
            	floatData.setDepth(rs.getFloat("STARTVALUE"));
            	if (rs.getObject("DECIMALVALUE")!=null) floatData.setValue(rs.getFloat("DECIMALVALUE"));
                return floatData;
            }
        };
        return this.jdbcTemplate.query(sql, mapper, logID, startSampleNo,endSampleNo);
    }
    
    /**
     * Gets Mask log data
     *
     * @param logID
     *            log identifier
     * @param startSampleNo
     *            starting sample number for the range of data to retrieve
     * @param endSampleNo
     *            ending sample number for the range of data to retrieve
     * @return List<MaskDataVo> a list of value objects that hold a mask log type data
     */
    public List<MaskDataVo> getMaskLogData(String logID, int startSampleNo,int endSampleNo) {
        String sql = "select DOMAINLOGDATA.samplenumber,DOMAINLOGDATA.STARTVALUE, MASKLOGDATA.MASKVALUE from domainlogdata inner join MASKLOGDATA on MASKLOGDATA.SAMPLENUMBER=DOMAINLOGDATA.SAMPLENUMBER INNER JOIN LOGS on MASKLOGDATA.log_id=LOGS.log_id WHERE MASKLOGDATA.LOG_ID=? and Domainlogdata.log_id = logs.domainlog_id AND Domainlogdata.samplenumber between ? and ? order by Domainlogdata.samplenumber";
        RowMapper<MaskDataVo> mapper = new RowMapper<MaskDataVo>() {
            public MaskDataVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
            	MaskDataVo maskData = new MaskDataVo();
            	maskData.setSamplenumber(rs.getInt("samplenumber"));
            	maskData.setDepth(rs.getFloat("STARTVALUE"));
            	if (rs.getObject("MASKVALUE")!=null) maskData.setValue(rs.getBoolean("MASKVALUE"));
                return maskData;
            }
        };
        return this.jdbcTemplate.query(sql, mapper, logID, startSampleNo,endSampleNo);
    }
    
    /**
     * Getting nearest sample number above to the depth value given
     *
     * @param domainid
     *            domainid for domain of interest
     * @param depth
     * 			  depth down hole
     * @return sample number.
     *
     */
    public List<Integer> getNearestSampleNumber(String domainid, float depth) {
        String sql = "select samplenumber from domainlogdata where log_id=? order by abs(?-startvalue),samplenumber asc";
        RowMapper<Integer> mapper = new RowMapper<Integer>() {
            public Integer mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                return rs.getInt("samplenumber");
            }
        };
        return this.jdbcTemplate.query(sql,mapper, domainid,depth);
    }
    
    /**
     * Getting the list standard algorithms
     *
     * @return AlgorithmCollectionVo value object consists of a list of algorithms
     *
     */
    public AlgorithmCollectionVo getAlgorithmsCollection() {
        String sql = "select algorithms.ALGORITHM_ID,algorithms.ALGORITHMNAME,algorithmoutputs.ALGORITHMOUTPUTNAME,algorithmoutputs.ALGORITHMOUTPUT_ID,algorithmoutputs.ALGVERSION from algorithms inner join algorithmoutputs on algorithms.ALGORITHM_ID=algorithmoutputs.algorithm_id ORDER by algorithmoutputs.ALGORITHMOUTPUT_ID";
        AlgorithmCollectionVo algcol = new AlgorithmCollectionVo();
        
        List<Map<String, Object>> rows = this.jdbcTemplate.queryForList(sql);  
        for (Map<String, Object> row : rows) {  
        	algcol.addAlgorithm((String)row.get("ALGORITHMNAME"),row.get("ALGORITHM_ID") instanceof BigDecimal ? ((BigDecimal)row.get("ALGORITHM_ID")).intValue():(Integer)row.get("ALGORITHM_ID") , (String)row.get("ALGORITHMOUTPUTNAME"), row.get("ALGORITHMOUTPUT_ID") instanceof BigDecimal ? ((BigDecimal)row.get("ALGORITHMOUTPUT_ID")).intValue():((Integer)row.get("ALGORITHMOUTPUT_ID")).intValue(),row.get("ALGVERSION") instanceof BigDecimal ? ((BigDecimal)row.get("ALGVERSION")).intValue():((Integer)row.get("ALGVERSION")).intValue()); 
        }  


        return algcol;
    }

	public ClassificationsCollectionVo getClassificationsCollection(int algoutputid) {
		String sql = "select INTINDEX, COLOUR, CLASSTEXT from CLASSIFICATIONS WHERE ALGORITHMOUTPUT_ID=?";
        RowMapper<ClassificationVo> mapper = new RowMapper<ClassificationVo>() {
            public ClassificationVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
            	ClassificationVo newclass = new ClassificationVo();
            	newclass.setIndex(rs.getInt("INTINDEX"));
            	newclass.setColour(rs.getInt("COLOUR"));
            	newclass.setClassText(rs.getString("CLASSTEXT"));
                return newclass;
            }
        };
        return new ClassificationsCollectionVo((ArrayList<ClassificationVo>) this.jdbcTemplate.query(sql,mapper, algoutputid));
	}

	public ClassificationsCollectionVo getClassificationsCollection(String logid) {
		String sql = "select INTINDEX, COLOUR, CLASSTEXT from CLASSSPECIFICCLASSIFICATIONS WHERE CLASSSPECIFICCLASSIFICATIONS.LOG_ID=? UNION ALL select INTINDEX, COLOUR, CLASSTEXT from CLASSIFICATIONS inner join LOGS on LOGS.ALGORITHMOUTPUT_ID=CLASSIFICATIONS.ALGORITHMOUTPUT_ID WHERE LOGS.LOG_ID=?";
        RowMapper<ClassificationVo> mapper = new RowMapper<ClassificationVo>() {
            public ClassificationVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
            	ClassificationVo newclass = new ClassificationVo();
            	newclass.setIndex(rs.getInt("INTINDEX"));
            	newclass.setColour(rs.getInt("COLOUR"));
            	newclass.setClassText(rs.getString("CLASSTEXT"));
                return newclass;
            }
        };
        return new ClassificationsCollectionVo((ArrayList<ClassificationVo>) this.jdbcTemplate.query(sql,mapper, logid,logid));
	}

    public String getDatasetIdfromLogId(String logId) {
        String sql = "select dataset_id from logs where log_id=?";
        return (String) this.jdbcTemplate.queryForObject(sql,String.class, logId);
    }

    public Integer getLastsampleNumber(String domainlogId, Integer startSampleNo, Integer endSampleNo) {
        String sql = "select max(samplenumber) from domainlogdata where log_id = ? "
        + "and samplenumber between ? and ? ";
        RowMapper<Integer> mapper = new RowMapper<Integer>() {
            public Integer mapRow(ResultSet rs, int rowNum)
                    throws SQLException, DataAccessException {
                return rs.getInt(1);
            }
        };
        return this.jdbcTemplate.queryForObject(sql,mapper, domainlogId, startSampleNo,endSampleNo);
    }

    public DomainDataCollectionVo getDomainDataRelativetoBaseDomain(String domainlogId) {
        String sql = "select DOMAINLOGDATA.samplenumber,DOMAINLOGDATA.STARTVALUE,DOMAINLOGDATA.ENDVALUE from domainlogdata WHERE Domainlogdata.log_id = ? order by Domainlogdata.samplenumber";
        RowMapper<DomainDataVo> mapper = new RowMapper<DomainDataVo>() {
            public DomainDataVo mapRow(ResultSet rs, int rowNum)
                    throws SQLException {
                        DomainDataVo domainData = new DomainDataVo();
            	domainData.setSampleNo(rs.getInt("samplenumber"));
            	domainData.setstartIndex(rs.getInt("STARTVALUE"));
                domainData.setendIndex(rs.getInt("ENDVALUE"));
                return domainData;
            }
        };
        return new DomainDataCollectionVo((ArrayList<DomainDataVo>) this.jdbcTemplate.query(sql, mapper, domainlogId));
    
    }

       public LogExtentsVo getlogExtents(String logId) {
        String sql = """
            SELECT
                CASE 
                    WHEN L.LOGTYPE = 0 THEN D.min_start
                    WHEN L.LOGTYPE = 1 THEN C.min_val
                    WHEN L.LOGTYPE = 2 THEN X.min_dec
                    ELSE 0
                END AS minval,
                CASE 
                    WHEN L.LOGTYPE = 0 THEN D.max_end
                    WHEN L.LOGTYPE = 1 THEN C.max_val
                    WHEN L.LOGTYPE = 2 THEN X.max_dec
                    ELSE 0
                END AS maxval
            FROM LOGS L
            LEFT JOIN (
                SELECT LOG_ID, MIN(STARTVALUE) AS min_start, MAX(ENDVALUE) AS max_end
                FROM DOMAINLOGDATA
                GROUP BY LOG_ID
            ) D ON D.LOG_ID = L.LOG_ID
            LEFT JOIN (
                SELECT LOG_ID, MIN(CLASSLOGVALUE) AS min_val, MAX(CLASSLOGVALUE) AS max_val
                FROM CLASSLOGDATA
                GROUP BY LOG_ID
            ) C ON C.LOG_ID = L.LOG_ID
            LEFT JOIN (
                SELECT LOG_ID, MIN(DECIMALVALUE) AS min_dec, MAX(DECIMALVALUE) AS max_dec
                FROM DECIMALLOGDATA
                GROUP BY LOG_ID
            ) X ON X.LOG_ID = L.LOG_ID
            WHERE L.LOG_ID = ?
            """;
        RowMapper<LogExtentsVo> mapper = new RowMapper<LogExtentsVo>() {
            public LogExtentsVo mapRow(ResultSet rs, int rowNum)
                throws SQLException {
                LogExtentsVo floatPair = new LogExtentsVo(rs.getFloat("minval"), rs.getFloat("maxval"));
                return floatPair;
            }
        };
        return this.jdbcTemplate.queryForObject(sql,mapper, logId);
    }

    public Optional<AlgorithmOutputVersionVo> findByNameAndVersion(AlgorithmCollectionVo algs, int algorithmId,String outputName,int version) {

        return algs.getAlgorithms().stream()
            .filter(a -> a.getAlgorithmID() == algorithmId)
            .findFirst()
            .flatMap(a -> a.getOutputs().stream()
                .filter(o -> outputName.equals(o.getName()))
                .findFirst())
            .flatMap(o -> o.getVersions().stream()
                .filter(v -> v.getVersion() == version)
                .findFirst());
    }

    public Optional<AlgorithmOutputVersionVo> findByOutputId(AlgorithmCollectionVo algs, int algorithmOutputId) {
        return algs.getAlgorithms().stream()
            .flatMap(a -> a.getOutputs().stream())
            .flatMap(o -> o.getVersions().stream()
                .filter(v -> v.getAlgorithmoutputID() == algorithmOutputId))
            .findFirst();

       }
}
