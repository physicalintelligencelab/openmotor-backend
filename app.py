"""
OpenMotor Research Tools - Backend API Server with Unified Endpoint

This Flask server provides API endpoints for the three OpenMotor tools:
1. CSV Standardizer
2. Description Template Filler  
3. README Generator

Plus a unified endpoint that runs all three in sequence.

Requirements:
pip install flask flask-cors pandas numpy anthropic PyPDF2 openpyxl PyMuPDF gunicorn beautifulsoup4==4.12.2 lxml==4.9.3
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pandas as pd
import numpy as np
import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime
import logging
from typing import Dict, List, Optional, Any, Tuple
import anthropic
from anthropic import Anthropic
import fitz  # PyMuPDF
from collections import Counter
import time
from PyPDF2 import PdfReader
import tempfile
import traceback
from werkzeug.utils import secure_filename
import hashlib
from dataclasses import dataclass
from difflib import SequenceMatcher
from openpyxl import Workbook
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin, quote
import time
import zipfile
import io

# just important the core functionality from modules

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

UPLOAD_FOLDER = tempfile.mkdtemp()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

@dataclass
class ColumnMapping:
    """Store column mapping information"""
    original_name: str
    standardized_name: str
    confidence: float
    reasoning: str
    data_type: str
    unit: str = ""
    transformation: str = ""
    is_custom: bool = False

class OpenMotorStandardizer:
    """CSV Standardizer implementation"""
    
    def __init__(self, api_key: str):
        self.client = Anthropic(api_key=api_key)
        self.required_columns = {
            'participant_idx': 'unique identifier for participants',
            'trial_number': 'trial number',
            'condition': 'experimental condition',
            'reaction_time': 'time to react in milliseconds',
            'movement_time': 'movement duration in milliseconds'
        }
        
        self.suggested_columns = {
            'device_type': 'device used to record data',
            'input_device': 'device used for response',
            'screen_height': 'screen height in cm',
            'screen_width': 'screen width in cm',
            'group': 'participant group',
            'participant_age': 'age of participant',
            'participant_sex': 'sex of participant',
            'participant_race': 'race of participant',
            'participant_ethnicity': 'ethnicity of participant',
            'dominant_hand': 'dominant hand',
            'assessment': 'cognitive assessment score',
            'medical_condition': 'neurological/physical condition',
            'years_of_education': 'education years',
            'participant_vision': 'vision status',
            'repeat_number': 'attempt number',
            'feedback': 'whether feedback given',
            'feedback_type': 'type of feedback',
            'feedback_modality': 'modality of feedback',
            'feedback_time': 'feedback duration in ms',
            'hand_angle': 'endpoint angle of hand in degrees',
            'hand_radius': 'hand radius',
            'initial_x': 'initial X coordinate',
            'initial_y': 'initial Y coordinate',
            'target_x': 'target X coordinate',
            'target_y': 'target Y coordinate',
            'target_angle': 'target angle in degrees',
            'target_radius': 'target radius',
            'target_height': 'height of target',
            'target_width': 'width of target',
            'target_type': 'type of target',
            'number_of_targets': 'number of targets',
            'click_x': 'click X coordinate',
            'click_y': 'click Y coordinate',
            'feedback_x': 'feedback X coordinate',
            'feedback_y': 'feedback Y coordinate',
            'rotation_angle': 'rotation angle in degrees',
            'rotation_direction': 'rotation direction',
            'perturbation_value': 'perturbation value',
            'solution_angle': 'solution angle',
            'solution_radius': 'solution radius',
            'researcher_id': 'researcher identifier',
            'reported_perturbation': 'reported perturbation',
            'true_perturbation': 'true perturbation',
            'correct_reported_perturbation': 'correct reported perturbation'
        }
        
        self.common_mappings = {
            'rt': 'reaction_time',
            'mt': 'movement_time',
            'sn': 'participant_idx',
            'subject_num': 'participant_idx',
            'subj': 'participant_idx',
            'tn': 'trial_number',
            'cn': 'condition',
            'bn': 'block_number',
            'cond': 'condition',
            'hand_theta': 'hand_angle',
            'tgt_ang': 'target_angle',
            'tgt_angle': 'target_angle',
            'target_ang': 'target_angle',
            'age': 'participant_age',
            'sex': 'participant_sex',
            'hand': 'dominant_hand',
            'handedness': 'dominant_hand',
            'education': 'years_of_education',
            'clamp': 'perturbation_value',
            'clampi': 'perturbation_value',
            'rotation': 'rotation_angle',
            'rot': 'rotation_angle',
            'perturb': 'perturbation_value',
            'fb': 'feedback',
            'fbi': 'feedback',
            'feedback_duration': 'feedback_time',
            'reach_amp': 'hand_radius',
            'reach_amplitude': 'hand_radius',
            'target_amp': 'target_radius',
            'target_dist': 'target_radius',
            'radial_distance': 'hand_radius',
            'angular_deviation': 'hand_angle',
            'heading_angle': 'hand_angle',
            'endpoint_angle': 'hand_angle',
            'movement_duration': 'movement_time',
            'reaction_latency': 'reaction_time',
            'response_time': 'reaction_time',
            'movement_onset': 'reaction_time',
            'reach_time': 'movement_time',
            'execution_time': 'movement_time'
        }
        
        self.all_standard_columns = {**self.required_columns, **self.suggested_columns}
        self.guessed_columns = []

    def read_pdf(self, pdf_path: str) -> str:
        """Extract text content from PDF"""
        try:
            doc = fitz.open(pdf_path)
            content = ""
            for page in doc:
                content += page.get_text()
            doc.close()
            return content[:10000]  # limit to first 10k chars :)
        except Exception as e:
            logger.error(f"Error reading PDF: {e}")
            return ""

    def analyze_csv_structure(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze the structure and content of the CSV"""
        analysis = {
            'total_rows': int(len(df)),
            'total_columns': int(len(df.columns)),
            'columns': {},
            'potential_issues': [],
            'likely_participant_cols': [],
            'likely_trial_cols': [],
            'likely_condition_cols': []
        }
        
        for col in df.columns:
            sample_values = df[col].dropna().head(10).tolist() if len(df[col].dropna()) > 0 else []
            sample_values = [
                float(x) if isinstance(x, (np.integer, np.floating)) else str(x) 
                for x in sample_values
            ]
            
            col_analysis = {
                'name': col,
                'dtype': str(df[col].dtype),
                'null_count': int(df[col].isnull().sum()),
                'null_percentage': float(df[col].isnull().sum() / len(df) * 100),
                'unique_values': int(df[col].nunique()),
                'sample_values': sample_values,
                'value_range': None,
                'likely_type': None
            }
            
            if pd.api.types.is_numeric_dtype(df[col]):
                col_numeric = pd.to_numeric(df[col], errors='coerce')
                if not col_numeric.isna().all():
                    col_analysis['value_range'] = {
                        'min': float(col_numeric.min()),
                        'max': float(col_numeric.max()),
                        'mean': float(col_numeric.mean()),
                        'std': float(col_numeric.std())
                    }
            
            analysis['columns'][col] = col_analysis
            
            col_lower = col.lower()
            if any(x in col_lower for x in ['subj', 'participant', 'sn', 'id']):
                analysis['likely_participant_cols'].append(col)
            elif any(x in col_lower for x in ['trial', 'tn', 'number']):
                analysis['likely_trial_cols'].append(col)
            elif any(x in col_lower for x in ['cond', 'cn', 'group', 'block']):
                analysis['likely_condition_cols'].append(col)
        
        return analysis

    def extract_paper_content(self, paper_content: Optional[str]) -> Dict[str, Any]:
        """Extract key information from paper using Claude"""
        if not paper_content:
            return {}
            
        prompt = f"""Extract key experimental information from this research paper that would help map CSV columns:

Paper content:
{paper_content[:10000]}

Extract:
1. Variable names and their abbreviations (e.g., "reaction time (RT)", "movement time (MT)")
2. Any mentions of column names or data variables
3. Units used (e.g., "times in milliseconds", "angles in degrees")
4. Experimental conditions and their labels

Return as JSON with keys: variable_mappings, units, conditions, abbreviations"""

        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            
            return json.loads(response.content[0].text)
        except Exception as e:
            logger.error(f"Error extracting paper content: {e}")
            return {}

    def get_aggressive_column_mapping(self, 
                                    csv_analysis: Dict[str, Any], 
                                    paper_info: Dict[str, Any],
                                    existing_mappings: set) -> List[ColumnMapping]:
        """Map columns using multiple strategies"""
        mappings = []
        unmapped_columns = []
        
        for col_name, col_info in csv_analysis['columns'].items():
            if col_name in existing_mappings:
                continue
                
            mapped = False
            col_lower = col_name.lower().strip()
            
            for abbrev, standard in self.common_mappings.items():
                if col_lower == abbrev or col_lower.replace('_', '') == abbrev:
                    if standard not in existing_mappings:
                        mappings.append(ColumnMapping(
                            original_name=col_name,
                            standardized_name=standard,
                            confidence=0.95,
                            reasoning=f"Common abbreviation: {abbrev} → {standard}",
                            data_type="numeric" if col_info['dtype'] != 'object' else "text",
                            unit=self._infer_unit(standard, col_info),
                            transformation=""
                        ))
                        existing_mappings.add(standard)
                        mapped = True
                        break
            
            if not mapped:
                unmapped_columns.append((col_name, col_info))
        
        if unmapped_columns:
            prompt = f"""Map these columns to OpenMotor standards. Be AGGRESSIVE in finding mappings.

Unmapped columns:
{json.dumps([{'name': c[0], 'info': c[1]} for c in unmapped_columns], indent=2)}

Standard columns available:
{json.dumps(self.all_standard_columns, indent=2)}

Return JSON array with mappings."""

            try:
                response = self.client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=4000,
                    temperature=0.1,
                    messages=[{"role": "user", "content": prompt}]
                )
                
                claude_mappings = json.loads(response.content[0].text)
                for m in claude_mappings:
                    if m['standardized_name'] not in existing_mappings:
                        mappings.append(ColumnMapping(
                            original_name=m['original_name'],
                            standardized_name=m['standardized_name'],
                            confidence=float(m.get('confidence', 0.7)),
                            reasoning=m.get('reasoning', ''),
                            data_type=m.get('data_type', 'text'),
                            unit=m.get('unit', ''),
                            transformation=m.get('transformation', ''),
                            is_custom=m['standardized_name'] not in self.all_standard_columns
                        ))
                        if m['standardized_name'] in self.all_standard_columns:
                            existing_mappings.add(m['standardized_name'])
            except Exception as e:
                logger.error(f"Error in aggressive mapping: {e}")
        
        return mappings

    def _infer_unit(self, standard_name: str, col_info: Dict[str, Any]) -> str:
        """Infer units based on standard column name and data"""
        if 'time' in standard_name:
            return 'milliseconds'
        elif 'angle' in standard_name or 'theta' in standard_name:
            return 'degrees'
        elif any(x in standard_name for x in ['x', 'y', 'height', 'width', 'radius']):
            return 'pixels_or_cm'
        return ""

    def standardize_csv(self, csv_path: str, pdf_path: Optional[str] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """Main method to standardize a CSV file"""
        logger.info(f"Starting standardization of {csv_path}")
        
        df = pd.read_csv(csv_path)
        logger.info(f"Loaded CSV with shape: {df.shape}")
        
        paper_info = {}
        if pdf_path and os.path.exists(pdf_path):
            paper_content = self.read_pdf(pdf_path)
            paper_info = self.extract_paper_content(paper_content)
        
        csv_analysis = self.analyze_csv_structure(df)
        
        existing_mappings = set()
        all_mappings = []
        
        new_mappings = self.get_aggressive_column_mapping(
            csv_analysis, paper_info, existing_mappings
        )
        all_mappings.extend(new_mappings)
        
        standardized_df = self.apply_transformations(df, all_mappings, paper_info)
        
        quality_report = self.generate_quality_report(df, standardized_df, all_mappings)
        
        return standardized_df, quality_report

    def apply_transformations(self, 
                            df: pd.DataFrame, 
                            mappings: List[ColumnMapping],
                            paper_info: Dict[str, Any] = None) -> pd.DataFrame:
        """Apply transformations to create standardized dataframe"""
        standardized_df = pd.DataFrame()
        
        for req_col in self.required_columns.keys():
            mapping = next((m for m in mappings if m.standardized_name == req_col), None)
            if mapping and mapping.original_name in df.columns:
                standardized_df[req_col] = df[mapping.original_name]
            else:
                standardized_df[req_col] = "Value was not sure"
        
        for sug_col in self.suggested_columns.keys():
            mapping = next((m for m in mappings if m.standardized_name == sug_col), None)
            if mapping and mapping.original_name in df.columns:
                standardized_df[sug_col] = df[mapping.original_name]
        
        for mapping in mappings:
            if mapping.is_custom and mapping.original_name in df.columns:
                standardized_df[mapping.standardized_name] = df[mapping.original_name]
        
        mapped_original_cols = {m.original_name for m in mappings}
        for col in df.columns:
            if col not in mapped_original_cols:
                standardized_name = col.lower().replace(' ', '_')
                standardized_df[standardized_name] = df[col]
        
        return standardized_df.fillna("NaN")

    def generate_quality_report(self, 
                              original_df: pd.DataFrame, 
                              standardized_df: pd.DataFrame,
                              mappings: List[ColumnMapping]) -> Dict[str, Any]:
        """Generate a comprehensive quality report"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'original_shape': list(original_df.shape),
            'standardized_shape': list(standardized_df.shape),
            'total_columns_mapped': len([m for m in mappings if not m.is_custom]),
            'custom_columns': len([m for m in mappings if m.is_custom]),
            'mappings_summary': {
                'total_mappings': len(mappings),
                'high_confidence': len([m for m in mappings if m.confidence >= 0.8]),
                'medium_confidence': len([m for m in mappings if 0.5 <= m.confidence < 0.8]),
                'low_confidence': len([m for m in mappings if m.confidence < 0.5])
            },
            'required_columns_status': {}
        }
        
        for req_col in self.required_columns:
            if req_col in standardized_df.columns:
                uncertain_count = (standardized_df[req_col] == "Value was not sure").sum()
                report['required_columns_status'][req_col] = {
                    'present': True,
                    'uncertain_values': int(uncertain_count),
                    'completeness': float(1 - (uncertain_count / len(standardized_df)))
                }
            else:
                report['required_columns_status'][req_col] = {
                    'present': False,
                    'uncertain_values': len(standardized_df),
                    'completeness': 0.0
                }
        
        return report


class OpenMotorTemplateFiller:
    """Template Filler implementation"""
    
    def __init__(self, api_key: str):
        self.client = Anthropic(api_key=api_key)
        self.template_fields = self._get_template_fields()
    
    def _get_template_fields(self) -> Dict[str, str]:
        return {
            'Category': 'Area of motor research',
            'Theory': 'Theory tested or discussed in paper',
            'Theory_driven': 'driven/mentions/relates to/post-hoc',
            'Theory_status': 'supports/challenges/neutral',
            'Country': 'Country where data was collected',
            'Name_in_database': 'CSV filename',
            'Journal_year': 'Year published/preprint/last edited',
            'Expt_in_paper': 'Which experiment in paper',
            'Num_subjects': 'Total number of subjects',
            'Num_tasks_x_conditions': 'Format: X task x Y conditions',
            'Block_size': 'Number of trials per block',
            'Movement_type': 'Type of movement',
            'Reaction_time_measurement': 'Definition of reaction time start',
            'Movement_time_measurement': 'Definition of movement time',
            'Assessments_used': 'Cognitive assessments or N/A',
            'Medical_condition': 'Patient conditions or N/A',
            'Notes': 'Additional study design notes'
        }
    
    def read_pdf(self, pdf_path: str) -> str:
        try:
            doc = fitz.open(pdf_path)
            content = ""
            for i, page in enumerate(doc):
                content += f"\n--- Page {i+1} ---\n"
                content += page.get_text()
            doc.close()
            return content.strip()
        except Exception as e:
            logger.error(f"Error reading PDF: {e}")
            return ""
    
    def analyze_csv(self, csv_path: str) -> Dict[str, Any]:
        try:
            df = pd.read_csv(csv_path)
            
            analysis = {
                'file_name': os.path.basename(csv_path),
                'total_rows': len(df),
                'columns': list(df.columns),
                'num_subjects': 'N/A',
                'num_conditions': 1,
                'most_common_block_size': 'N/A'
            }
            
            participant_cols = [col for col in df.columns if any(
                x in col.lower() for x in ['participant', 'subject', 'subj', 'sn', 'id']
            )]
            if participant_cols:
                analysis['num_subjects'] = df[participant_cols[0]].nunique()
            
            condition_cols = [col for col in df.columns if any(
                x in col.lower() for x in ['condition', 'cond', 'group']
            )]
            if condition_cols:
                analysis['num_conditions'] = df[condition_cols[0]].nunique()
            
            block_cols = [col for col in df.columns if 'block' in col.lower()]
            if block_cols and participant_cols:
                block_sizes = df.groupby(block_cols[0]).size()
                analysis['most_common_block_size'] = block_sizes.mode()[0] if len(block_sizes) > 0 else 'N/A'
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing CSV: {e}")
            return {'error': str(e), 'file_name': os.path.basename(csv_path)}
    
    def extract_template_values(self, pdf_content: str, csv_analysis: Dict[str, Any], 
                               experiment_number: int) -> Dict[str, Any]:
        """Extract template values using Claude"""
        
        prompt = f"""Extract information for the OpenMotor Description Template from this research paper for Experiment {experiment_number}.

PDF Content:
{pdf_content[:15000]}

CSV Analysis:
{json.dumps(csv_analysis, indent=2)}

Return as JSON with these keys:
{json.dumps(list(self.template_fields.keys()))}

Use "N/A" for unavailable fields. Theory_driven must be one of: driven/mentions/relates to/post-hoc
Theory_status must be one of: supports/challenges/neutral"""

        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            
            result = json.loads(response.content[0].text)
            
            result['Name_in_database'] = csv_analysis.get('file_name', '')
            result['Num_subjects'] = str(csv_analysis.get('num_subjects', 'N/A'))
            result['Block_size'] = str(csv_analysis.get('most_common_block_size', 'N/A'))
            result['Expt_in_paper'] = f'Experiment {experiment_number}'
            
            if 'num_conditions' in csv_analysis:
                result['Num_tasks_x_conditions'] = f"1 task x {csv_analysis['num_conditions']} conditions"
            
            return result
            
        except Exception as e:
            logger.error(f"Error extracting template values: {e}")
            return {}
    
    def process_experiments(self, pdf_path: Optional[str], csv_paths: List[str]) -> pd.DataFrame:
        """Process multiple experiments and create DataFrame"""
        all_entries = []
        
        pdf_content = ""
        if pdf_path and os.path.exists(pdf_path):
            pdf_content = self.read_pdf(pdf_path)
        
        for i, csv_path in enumerate(csv_paths, 1):
            csv_analysis = self.analyze_csv(csv_path)
            
            if 'error' in csv_analysis:
                continue
            
            extracted_values = self.extract_template_values(
                pdf_content, csv_analysis, i
            )
            
            for field in self.template_fields:
                if field not in extracted_values:
                    extracted_values[field] = 'N/A'
            
            all_entries.append(extracted_values)
        
        return pd.DataFrame(all_entries)


class OpenMotorReadmeGenerator:
    """README Generator implementation"""
    
    def __init__(self, api_key: str):
        self.client = Anthropic(api_key=api_key)
    
    def read_pdf(self, pdf_path: str) -> str:
        try:
            reader = PdfReader(pdf_path)
            pdf_text = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
            return pdf_text
        except Exception as e:
            logger.error(f"Error reading PDF: {e}")
            return ""
    
    def generate_readme(self, pdf_content: str, experiment_number: int) -> str:
        """Generate README for specific experiment"""
        
        prompt = f"""Generate a complete README file for Experiment {experiment_number} from this research paper.

Include these sections:
- **Citation or title** (add "(Experiment {experiment_number})" at the end)
- **Key results** (4-8 sentences about Experiment {experiment_number} only)
- **Authors** (with emails if available)
- **Methods** (detailed description of Experiment {experiment_number})
- **Theory notes**
- **Assessments**
- **Medical condition**
- **Exclusion criteria**
- **Explanation of missing data**
- **Participant demographics**
- **Experiment goal** (4-6 sentences)
- **Special instructions**
- **Link to materials or code**

Paper content:
{pdf_content[:20000]}

If you cannot find Experiment {experiment_number}, return "EXPERIMENT_NOT_FOUND"."""

        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4000,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            )
            
            return response.content[0].text
            
        except Exception as e:
            logger.error(f"Error generating README: {e}")
            return f"Error: {str(e)}"


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/process-all', methods=['POST'])
def process_all():
    """Unified endpoint that processes everything in one go"""
    try:
        if 'api_key' not in request.form:
            return jsonify({'error': 'API key is required'}), 400
        
        api_key = request.form['api_key']
        pdf_file = request.files.get('pdf_file')
        
        # Collect all CSV files
        csv_files = []
        for key in request.files:
            if key.startswith('csv_file_'):
                csv_files.append(request.files[key])
        
        if not csv_files:
            return jsonify({'error': 'At least one CSV file is required'}), 400
        
        # Save uploaded files
        pdf_path = None
        if pdf_file:
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(pdf_file.filename))
            pdf_file.save(pdf_path)
        
        csv_paths = []
        for csv_file in csv_files:
            csv_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(csv_file.filename))
            csv_file.save(csv_path)
            csv_paths.append(csv_path)
        
        results = {
            'standardized_files': [],
            'template_file': None,
            'readme_files': [],
            'zip_file': None
        }
        
        # Initialize processors
        standardizer = OpenMotorStandardizer(api_key)
        filler = OpenMotorTemplateFiller(api_key)
        generator = OpenMotorReadmeGenerator(api_key)
        
        # Step 1: Standardize all CSVs
        standardized_paths = []
        for i, csv_path in enumerate(csv_paths, 1):
            try:
                standardized_df, quality_report = standardizer.standardize_csv(csv_path, pdf_path)
                
                # Save standardized CSV
                output_filename = f'standardized_exp{i}.csv'
                output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
                standardized_df.to_csv(output_path, index=False)
                standardized_paths.append(output_path)
                
                results['standardized_files'].append({
                    'filename': output_filename,
                    'experiment_number': i,
                    'report': quality_report,
                    'download_url': f'/api/download/{output_filename}'
                })
            except Exception as e:
                logger.error(f"Error standardizing CSV {i}: {str(e)}")
                results['standardized_files'].append({
                    'filename': None,
                    'experiment_number': i,
                    'error': str(e)
                })
        
        # Step 2: Fill template using standardized CSVs
        try:
            template_df = filler.process_experiments(pdf_path, standardized_paths)
            template_filename = 'OpenMotor_Description_Template_Filled.xlsx'
            template_path = os.path.join(app.config['UPLOAD_FOLDER'], template_filename)
            template_df.to_excel(template_path, index=False)
            
            results['template_file'] = {
                'filename': template_filename,
                'experiments_processed': len(template_df),
                'download_url': f'/api/download/{template_filename}'
            }
        except Exception as e:
            logger.error(f"Error filling template: {str(e)}")
            results['template_file'] = {'error': str(e)}
        
        # Step 3: Generate README files
        pdf_content = ""
        if pdf_path:
            pdf_content = generator.read_pdf(pdf_path)
        
        for i in range(1, len(csv_paths) + 1):
            try:
                readme_content = generator.generate_readme(pdf_content, i)
                
                if readme_content.strip() != "EXPERIMENT_NOT_FOUND":
                    readme_filename = f'Exp{i}_readme.txt'
                    readme_path = os.path.join(app.config['UPLOAD_FOLDER'], readme_filename)
                    
                    with open(readme_path, 'w', encoding='utf-8') as f:
                        f.write(readme_content)
                    
                    results['readme_files'].append({
                        'filename': readme_filename,
                        'experiment_number': i,
                        'download_url': f'/api/download/{readme_filename}',
                        'preview': readme_content[:500] + '...' if len(readme_content) > 500 else readme_content
                    })
                else:
                    results['readme_files'].append({
                        'filename': None,
                        'experiment_number': i,
                        'error': f'Experiment {i} not found in paper'
                    })
            except Exception as e:
                logger.error(f"Error generating README for experiment {i}: {str(e)}")
                results['readme_files'].append({
                    'filename': None,
                    'experiment_number': i,
                    'error': str(e)
                })
        
        # Step 4: Create a zip file with all outputs
        try:
            zip_filename = f'OpenMotor_Results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
            zip_path = os.path.join(app.config['UPLOAD_FOLDER'], zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                # Add standardized CSVs
                for item in results['standardized_files']:
                    if item.get('filename'):
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], item['filename'])
                        if os.path.exists(file_path):
                            zipf.write(file_path, item['filename'])
                
                # Add template
                if results['template_file'] and results['template_file'].get('filename'):
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], results['template_file']['filename'])
                    if os.path.exists(file_path):
                        zipf.write(file_path, results['template_file']['filename'])
                
                # Add READMEs
                for item in results['readme_files']:
                    if item.get('filename'):
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], item['filename'])
                        if os.path.exists(file_path):
                            zipf.write(file_path, item['filename'])
            
            results['zip_file'] = {
                'filename': zip_filename,
                'download_url': f'/api/download/{zip_filename}'
            }
        except Exception as e:
            logger.error(f"Error creating zip file: {str(e)}")
            results['zip_file'] = {'error': str(e)}
        
        # Clean up original uploaded files
        for csv_path in csv_paths:
            if os.path.exists(csv_path):
                os.remove(csv_path)
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)
        
        return jsonify({
            'success': True,
            'results': results,
            'total_experiments': len(csv_paths)
        })
        
    except Exception as e:
        logger.error(f"Error in process_all: {str(e)}")
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/api/standardize', methods=['POST'])
def standardize_csv():
    """Standardize CSV endpoint"""
    try:
        if 'api_key' not in request.form:
            return jsonify({'error': 'API key is required'}), 400
        
        if 'csv_file' not in request.files:
            return jsonify({'error': 'CSV file is required'}), 400
        
        api_key = request.form['api_key']
        csv_file = request.files['csv_file']
        pdf_file = request.files.get('pdf_file')
        
        csv_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(csv_file.filename))
        csv_file.save(csv_path)
        
        pdf_path = None
        if pdf_file:
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(pdf_file.filename))
            pdf_file.save(pdf_path)
        
        standardizer = OpenMotorStandardizer(api_key)
        standardized_df, quality_report = standardizer.standardize_csv(csv_path, pdf_path)
        
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'standardized_output.csv')
        standardized_df.to_csv(output_path, index=False)
        
        os.remove(csv_path)
        if pdf_path:
            os.remove(pdf_path)
        
        return jsonify({
            'success': True,
            'standardized_file': 'standardized_output.csv',
            'report': quality_report,
            'download_url': f'/api/download/standardized_output.csv'
        })
        
    except Exception as e:
        logger.error(f"Error in standardize_csv: {str(e)}")
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/api/fill-template', methods=['POST'])
def fill_template():
    """Fill template endpoint"""
    try:
        if 'api_key' not in request.form:
            return jsonify({'error': 'API key is required'}), 400
        
        api_key = request.form['api_key']
        pdf_file = request.files.get('pdf_file')
        
        csv_files = []
        for key in request.files:
            if key.startswith('csv_file_'):
                csv_files.append(request.files[key])
        
        if not csv_files:
            return jsonify({'error': 'At least one CSV file is required'}), 400
        
        pdf_path = None
        if pdf_file:
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(pdf_file.filename))
            pdf_file.save(pdf_path)
        
        csv_paths = []
        for csv_file in csv_files:
            csv_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(csv_file.filename))
            csv_file.save(csv_path)
            csv_paths.append(csv_path)
        
        filler = OpenMotorTemplateFiller(api_key)
        df = filler.process_experiments(pdf_path, csv_paths)
        
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'OpenMotor_Description_Template_Filled.xlsx')
        df.to_excel(output_path, index=False)
        
        for csv_path in csv_paths:
            os.remove(csv_path)
        if pdf_path:
            os.remove(pdf_path)
        
        return jsonify({
            'success': True,
            'output_file': 'OpenMotor_Description_Template_Filled.xlsx',
            'experiments_processed': len(df),
            'download_url': f'/api/download/OpenMotor_Description_Template_Filled.xlsx'
        })
        
    except Exception as e:
        logger.error(f"Error in fill_template: {str(e)}")
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/api/generate-readme', methods=['POST'])
def generate_readme():
    """Generate README endpoint"""
    try:
        if 'api_key' not in request.form:
            return jsonify({'error': 'API key is required'}), 400
        
        if 'pdf_file' not in request.files:
            return jsonify({'error': 'PDF file is required'}), 400
        
        api_key = request.form['api_key']
        pdf_file = request.files['pdf_file']
        experiment_number = int(request.form.get('experiment_number', 1))
        
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(pdf_file.filename))
        pdf_file.save(pdf_path)
        
        generator = OpenMotorReadmeGenerator(api_key)
        pdf_content = generator.read_pdf(pdf_path)
        readme_content = generator.generate_readme(pdf_content, experiment_number)
        
        if readme_content.strip() == "EXPERIMENT_NOT_FOUND":
            os.remove(pdf_path)
            return jsonify({
                'error': f'Experiment {experiment_number} not found in the paper'
            }), 404
        
        filename = f'Exp{experiment_number}_readme.txt'
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(readme_content)
        
        os.remove(pdf_path)
        
        return jsonify({
            'success': True,
            'filename': filename,
            'experiment_number': experiment_number,
            'content_preview': readme_content[:500] + '...' if len(readme_content) > 500 else readme_content,
            'download_url': f'/api/download/{filename}'
        })
        
    except Exception as e:
        logger.error(f"Error in generate_readme: {str(e)}")
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

@app.route('/api/download/<filename>', methods=['GET'])
def download_file(filename):
    """Download generated files"""
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True, download_name=filename)
        else:
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cleanup', methods=['POST'])
def cleanup_files():
    """Clean up temporary files older than 1 hour"""
    try:
        current_time = time.time()
        deleted_count = 0
        
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(file_path):
                file_modified = os.path.getmtime(file_path)
                if current_time - file_modified > 3600: 
                    os.remove(file_path)
                    deleted_count += 1
        
        return jsonify({
            'success': True,
            'deleted_files': deleted_count
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/fetch-pdf', methods=['POST'])
def fetch_pdf():
    """Fetch PDF from multiple sources"""
    try:
        data = request.json
        title = data.get('title', '')
        doi = data.get('doi', '')
        authors = data.get('authors', '')
        year = data.get('year', '')
        
        logger.info(f"Searching for PDF: {title}")
        
        # Try multiple methods to find the PDF
        pdf_url = None
        source = None
        
        # 1. Try Google Scholar first
        if not pdf_url:
            logger.info("Trying Google Scholar...")
            pdf_url = search_google_scholar(title)
            if pdf_url:
                source = "Google Scholar"
        
        # 2. Try Sci-Hub (multiple mirrors)
        if not pdf_url and doi:
            logger.info("Trying Sci-Hub...")
            pdf_url = try_scihub(doi)
            if pdf_url:
                source = "Sci-Hub"
        
        # 3. Try PubMed Central
        if not pdf_url:
            logger.info("Trying PubMed Central...")
            pdf_url = search_pmc(title)
            if pdf_url:
                source = "PubMed Central"
        
        # 4. Try arXiv
        if not pdf_url:
            logger.info("Trying arXiv...")
            pdf_url = search_arxiv(title)
            if pdf_url:
                source = "arXiv"
        
        # 5. Try bioRxiv/medRxiv
        if not pdf_url:
            logger.info("Trying bioRxiv...")
            pdf_url = search_biorxiv(title)
            if pdf_url:
                source = "bioRxiv"
        
        # 6. Try CORE
        if not pdf_url:
            logger.info("Trying CORE...")
            pdf_url = search_core(title)
            if pdf_url:
                source = "CORE"
        
        # 7. Try ResearchGate
        if not pdf_url:
            logger.info("Trying ResearchGate...")
            pdf_url = search_researchgate(title)
            if pdf_url:
                source = "ResearchGate"
        
        if pdf_url:
            # Download the PDF
            logger.info(f"Found PDF at {pdf_url} from {source}")
            pdf_content = download_pdf(pdf_url)
            
            if pdf_content:
                # Save temporarily
                filename = f"{title[:50].replace(' ', '_').replace('/', '_')}.pdf"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                
                with open(filepath, 'wb') as f:
                    f.write(pdf_content)
                
                return jsonify({
                    'success': True,
                    'filename': filename,
                    'source': source,
                    'size': len(pdf_content)
                })
        
        return jsonify({
            'success': False,
            'error': 'Could not find PDF'
        })
        
    except Exception as e:
        logger.error(f"Error fetching PDF: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def search_google_scholar(title):
    """Search Google Scholar for PDF"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Search Google Scholar
        search_url = f"https://scholar.google.com/scholar?q={quote(title)}"
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for PDF links
            for link in soup.find_all('a'):
                href = link.get('href', '')
                if '[PDF]' in link.text or href.endswith('.pdf'):
                    if href.startswith('http'):
                        return href
                    elif href.startswith('/'):
                        return f"https://scholar.google.com{href}"
            
            # Check for direct PDF links in the results
            for div in soup.find_all('div', class_='gs_or_ggsm'):
                link = div.find('a')
                if link and link.get('href'):
                    return link['href']
                    
    except Exception as e:
        logger.error(f"Google Scholar search error: {e}")
    
    return None

def try_scihub(doi):
    """Try to get PDF from Sci-Hub mirrors"""
    mirrors = [
        'https://sci-hub.se/',
        'https://sci-hub.st/',
        'https://sci-hub.ru/',
        'https://sci-hub.cat/',
        'https://sci-hub.tw/'
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    for mirror in mirrors:
        try:
            url = mirror + doi
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for PDF embed or iframe
                pdf_elem = soup.find('embed', {'type': 'application/pdf'}) or \
                          soup.find('iframe', {'id': 'pdf'})
                
                if pdf_elem and pdf_elem.get('src'):
                    pdf_url = pdf_elem['src']
                    if pdf_url.startswith('//'):
                        pdf_url = 'https:' + pdf_url
                    elif pdf_url.startswith('/'):
                        pdf_url = mirror + pdf_url[1:]
                    return pdf_url
                    
        except Exception as e:
            continue
    
    return None

def search_pmc(title):
    """Search PubMed Central for PDF"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        # Search PMC
        search_url = f"https://www.ncbi.nlm.nih.gov/pmc/?term={quote(title)}"
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find first result
            first_result = soup.find('div', class_='rslt')
            if first_result:
                # Get PMC ID
                pmc_link = first_result.find('a', class_='view')
                if pmc_link and 'PMC' in pmc_link.text:
                    pmc_id = re.search(r'PMC\d+', pmc_link.text)
                    if pmc_id:
                        # Direct PDF link
                        pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id.group()}/pdf/"
                        return pdf_url
                        
    except Exception as e:
        logger.error(f"PMC search error: {e}")
    
    return None

def search_arxiv(title):
    """Search arXiv for PDF"""
    try:
        import urllib.parse
        
        # Use arXiv API
        query = urllib.parse.quote(title)
        api_url = f"http://export.arxiv.org/api/query?search_query=ti:{query}&max_results=1"
        
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            # Parse XML response
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.text)
            
            # Find PDF link
            for entry in root.findall('{http://www.w3.org/2005/Atom}entry'):
                for link in entry.findall('{http://www.w3.org/2005/Atom}link'):
                    if link.get('title') == 'pdf':
                        return link.get('href')
                        
    except Exception as e:
        logger.error(f"arXiv search error: {e}")
    
    return None

def search_biorxiv(title):
    """Search bioRxiv for PDF"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        # Search bioRxiv
        search_url = f"https://www.biorxiv.org/search/{quote(title)}"
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find first result
            result = soup.find('a', class_='highwire-cite-linked-title')
            if result:
                article_url = 'https://www.biorxiv.org' + result['href']
                # Convert to PDF URL
                pdf_url = article_url + '.full.pdf'
                return pdf_url
                
    except Exception as e:
        logger.error(f"bioRxiv search error: {e}")
    
    return None

def search_core(title):
    """Search CORE for PDF"""
    try:
        # CORE API (no key needed for basic search)
        api_url = f"https://api.core.ac.uk/v3/search/works?q={quote(title)}&limit=1"
        
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('results') and len(data['results']) > 0:
                result = data['results'][0]
                if result.get('downloadUrl'):
                    return result['downloadUrl']
                    
    except Exception as e:
        logger.error(f"CORE search error: {e}")
    
    return None

def search_researchgate(title):
    """Search ResearchGate for PDF"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        search_url = f"https://www.researchgate.net/search/publication?q={quote(title)}"
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            # Look for PDF download links in the HTML
            if 'rgcdn.net' in response.text and '.pdf' in response.text:
                # Extract PDF URL using regex
                pdf_pattern = r'(https://.*?rgcdn\.net/.*?\.pdf)'
                match = re.search(pdf_pattern, response.text)
                if match:
                    return match.group(1)
                    
    except Exception as e:
        logger.error(f"ResearchGate search error: {e}")
    
    return None

def download_pdf(url):
    """Download PDF from URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=30, stream=True)
        
        if response.status_code == 200:
            # Check if it's actually a PDF
            content_type = response.headers.get('content-type', '')
            if 'pdf' in content_type or url.endswith('.pdf'):
                return response.content
            
            # Sometimes PDFs are served without proper content-type
            # Check first few bytes for PDF signature
            first_bytes = response.content[:5]
            if first_bytes == b'%PDF-':
                return response.content
                
    except Exception as e:
        logger.error(f"PDF download error: {e}")
    
    return None

# Also add BeautifulSoup to requirements
# pip install beautifulsoup4


if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    app.run(debug=True, host='0.0.0.0', port=5001)
