import { useRef, useState } from 'react'
import { ReportUploadError, processReport, uploadReport } from '../../services/reportService.js'
import './ReportsPage.css'

function ReportsPage() {
  const fileInputRef = useRef(null)

  const [selectedFile, setSelectedFile] = useState(null)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [uploadedReport, setUploadedReport] = useState(null)
  const [isProcessing, setIsProcessing] = useState(false)
  const [processError, setProcessError] = useState('')

  function handleFileChange(event) {
    const file = event.target.files?.[0] ?? null
    setSelectedFile(file)
    setUploadError('')
    setUploadedReport(null)
    setProcessError('')
  }

  async function handleUpload() {
    if (!selectedFile || isUploading) return

    setIsUploading(true)
    setUploadError('')

    try {
      const report = await uploadReport(selectedFile)
      setUploadedReport(report)
      setSelectedFile(null)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    } catch (error) {
      setUploadError(
        error instanceof ReportUploadError
          ? error.message
          : 'Something went wrong uploading your report. Please try again.',
      )
    } finally {
      setIsUploading(false)
    }
  }

  async function handleProcess() {
    if (!uploadedReport || isProcessing) return

    setIsProcessing(true)
    setProcessError('')

    try {
      const report = await processReport(uploadedReport.id)
      setUploadedReport(report)
    } catch (error) {
      setProcessError(
        error instanceof ReportUploadError
          ? error.message
          : 'Something went wrong processing your report. Please try again.',
      )
    } finally {
      setIsProcessing(false)
    }
  }

  return (
    <div className="reports-page">
      <span className="eyebrow">Patient Dashboard</span>
      <h1>Medical Reports</h1>
      <p className="section-intro">
        Upload a medical report as a PDF. MediSync stores it securely for you.
      </p>

      <section className="dashboard-card reports-upload-card">
        <label htmlFor="report-file" className="reports-upload-label">
          Select PDF
        </label>
        <input
          id="report-file"
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          onChange={handleFileChange}
          disabled={isUploading}
        />

        {selectedFile && <p className="reports-selected-filename">{selectedFile.name}</p>}

        {uploadError && <p className="field-error">{uploadError}</p>}

        {uploadedReport && (
          <div className="reports-upload-success">
            <p>
              Uploaded "{uploadedReport.original_filename}" — status: {uploadedReport.status}
            </p>
            {uploadedReport.status === 'uploaded' && (
              <button
                type="button"
                className="btn btn-secondary reports-process-btn"
                onClick={handleProcess}
                disabled={isProcessing}
              >
                {isProcessing ? 'Processing…' : 'Process Report'}
              </button>
            )}
          </div>
        )}

        {processError && <p className="field-error">{processError}</p>}

        <button
          type="button"
          className="btn btn-primary reports-upload-btn"
          onClick={handleUpload}
          disabled={!selectedFile || isUploading}
        >
          {isUploading ? 'Uploading…' : 'Upload Report'}
        </button>
      </section>

      <p className="dashboard-card-empty">Your uploaded medical reports will appear here.</p>
    </div>
  )
}

export default ReportsPage
