# visualizations.py
"""
COMPLETE VISUALIZATIONS MODULE USING ALL DATA SCIENCE PACKAGES
- Plotly: Interactive web charts
- Dash: Interactive dashboards  
- Polars: Fast data processing
- Transformers: NLP for text analysis
- Matplotlib: Static export charts
- Seaborn: Statistical visualizations
- NumPy: Numerical computing
"""

import plotly.graph_objects as go
import plotly.express as px
import plotly.io as pio
import dash
from dash import dcc, html, Input, Output, State, callback_context
import dash_bootstrap_components as dbc
import polars as pl
import pandas as pd
import numpy as np
from transformers import pipeline
from sentence_transformers import SentenceTransformer
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, List, Optional, Any, Union, Tuple
import json
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass, asdict
import uuid
import base64
from io import BytesIO
import warnings
warnings.filterwarnings('ignore')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize global models (lazy loaded)
_TRANSFORMER_MODELS = {}

@dataclass
class VisualizationConfig:
    """Configuration for all visualization types"""
    # Plotly settings
    plotly_template: str = "plotly_white"
    plotly_width: int = 1200
    plotly_height: int = 700
    
    # Matplotlib settings
    matplotlib_style: str = "seaborn-v0_8-whitegrid"
    matplotlib_dpi: int = 300
    
    # Seaborn settings
    seaborn_palette: str = "husl"
    
    # Dash settings
    dash_theme: str = "bootstrap"
    dash_port: int = 8050
    
    # Polars settings
    polars_streaming: bool = True
    
    # Transformers settings
    transformers_device: str = "cpu"
    transformers_batch_size: int = 16

class AdvancedVisualizer:
    """
    Main visualizer class using ALL data science packages
    Each method demonstrates a different package's power
    """
    
    def __init__(self, db_connection=None):
        self.db = db_connection
        self.config = VisualizationConfig()
        
        # Setup matplotlib
        plt.style.use(self.config.matplotlib_style)
        
        # Setup seaborn
        sns.set_palette(self.config.seaborn_palette)
        
        # Setup plotly
        pio.templates.default = self.config.plotly_template
        
        # Initialize transformers lazily
        self._transformer_models = {}
        
        logger.info("✅ AdvancedVisualizer initialized with all packages")
    
    # ========== POLARS: ULTRA-FAST DATA PROCESSING ==========
    
    def process_with_polars(self, data: List[Dict]) -> pl.DataFrame:
        """
        Use Polars for lightning-fast data processing
        10-100x faster than pandas for large datasets
        """
        logger.info("🚀 Processing data with Polars...")
        
        # Convert to Polars DataFrame
        df = pl.DataFrame(data)
        
        # Show Polars capabilities
        logger.info(f"📊 Polars processing:")
        logger.info(f"   Shape: {df.shape}")
        logger.info(f"   Columns: {df.columns}")
        logger.info(f"   Memory usage: {df.estimated_size() / 1024 / 1024:.2f} MB")
        
        # Perform complex operations efficiently
        if 'availability' in df.columns and 'department' in df.columns:
            # Lazy evaluation for optimal performance
            result = (
                df.lazy()
                .group_by("department")
                .agg([
                    pl.col("availability").mean().alias("avg_availability"),
                    pl.col("availability").std().alias("std_availability"),
                    pl.col("availability").quantile(0.25).alias("q1_availability"),
                    pl.col("availability").quantile(0.75).alias("q3_availability"),
                    pl.count().alias("equipment_count")
                ])
                .sort("avg_availability", descending=True)
                .collect()
            )
            
            logger.info("✅ Polars processing complete")
            return result
        
        return df
    
    # ========== TRANSFORMERS: NLP & AI VISUALIZATIONS ==========
    
    def _get_transformer_model(self, model_name: str = "distilbert-base-uncased"):
        """Lazy load transformer models"""
        if model_name not in self._transformer_models:
            try:
                logger.info(f"🤖 Loading transformer model: {model_name}")
                
                if model_name == "sentiment":
                    self._transformer_models[model_name] = pipeline(
                        "sentiment-analysis",
                        model="distilbert-base-uncased-finetuned-sst-2-english",
                        device=self.config.transformers_device
                    )
                elif model_name == "text-generation":
                    self._transformer_models[model_name] = pipeline(
                        "text-generation",
                        model="gpt2",
                        device=self.config.transformers_device
                    )
                elif model_name == "embeddings":
                    self._transformer_models[model_name] = SentenceTransformer(
                        'all-MiniLM-L6-v2'
                    )
                
                logger.info(f"✅ Transformer model '{model_name}' loaded")
            except Exception as e:
                logger.error(f"❌ Failed to load transformer model: {e}")
                return None
        
        return self._transformer_models.get(model_name)
    
    def analyze_text_with_transformers(self, texts: List[str]) -> Dict:
        """
        Use transformers for NLP analysis of maintenance notes, reports, etc.
        """
        logger.info("🧠 Analyzing text with Transformers...")
        
        sentiment_analyzer = self._get_transformer_model("sentiment")
        embedding_model = self._get_transformer_model("embeddings")
        
        if not sentiment_analyzer:
            return {"error": "Transformers not available"}
        
        results = []
        
        # Batch process for efficiency
        batch_size = self.config.transformers_batch_size
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            try:
                # Sentiment analysis
                sentiments = sentiment_analyzer(batch)
                
                # Text embeddings for clustering
                if embedding_model:
                    embeddings = embedding_model.encode(batch)
                else:
                    embeddings = None
                
                for j, (text, sentiment) in enumerate(zip(batch, sentiments)):
                    if len(text.strip()) > 3:  # Ignore very short texts
                        results.append({
                            "text": text[:200] + "..." if len(text) > 200 else text,
                            "sentiment": sentiment["label"],
                            "sentiment_score": float(sentiment["score"]),
                            "embedding": embeddings[j].tolist() if embeddings else None,
                            "text_length": len(text),
                            "word_count": len(text.split())
                        })
            except Exception as e:
                logger.error(f"Error processing text batch: {e}")
                continue
        
        logger.info(f"✅ Analyzed {len(results)} texts with Transformers")
        return {
            "analyses": results,
            "total_texts": len(results),
            "positive_count": sum(1 for r in results if r["sentiment"] == "POSITIVE"),
            "negative_count": sum(1 for r in results if r["sentiment"] == "NEGATIVE")
        }
    
    # ========== PLOTLY: INTERACTIVE WEB VISUALIZATIONS ==========
    
    def create_interactive_dashboard(self, data: List[Dict]) -> Dict:
        """
        Create a complete interactive dashboard with Plotly
        """
        logger.info("📊 Creating interactive Plotly dashboard...")
        
        df = pd.DataFrame(data)
        
        # Create multiple coordinated charts
        charts = {}
        
        # 1. 3D Scatter Plot with Polars clustering
        if len(df) > 10:
            charts["3d_cluster"] = self._create_3d_cluster_plot(df)
        
        # 2. Parallel Coordinates Plot
        if len(df.columns) >= 4:
            charts["parallel_coords"] = self._create_parallel_coordinates(df)
        
        # 3. Sunburst Chart (Hierarchical)
        charts["sunburst"] = self._create_sunburst_chart(df)
        
        # 4. Animated Time Series
        charts["animated"] = self._create_animated_timeseries(df)
        
        # 5. Violin + Box Plot Combination
        charts["violin_box"] = self._create_violin_box_plot(df)
        
        logger.info(f"✅ Created {len(charts)} interactive Plotly charts")
        return charts
    
    def _create_3d_cluster_plot(self, df: pd.DataFrame) -> Dict:
        """Create 3D scatter plot with clustering"""
        from sklearn.cluster import DBSCAN
        from sklearn.preprocessing import StandardScaler
        
        # Select numerical columns
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if len(numeric_cols) < 3:
            return {"error": "Need at least 3 numeric columns"}
        
        # Use first 3 numeric columns
        features = df[numeric_cols[:3]].fillna(0)
        
        # Cluster the data
        scaler = StandardScaler()
        scaled = scaler.fit_transform(features)
        
        # DBSCAN clustering
        clustering = DBSCAN(eps=0.5, min_samples=3).fit(scaled)
        labels = clustering.labels_
        
        # Create 3D plot
        fig = px.scatter_3d(
            x=features.iloc[:, 0],
            y=features.iloc[:, 1],
            z=features.iloc[:, 2],
            color=labels.astype(str),
            title="🤖 3D Clustering Analysis (DBSCAN)",
            labels={
                'x': numeric_cols[0],
                'y': numeric_cols[1],
                'z': numeric_cols[2]
            },
            color_discrete_sequence=px.colors.qualitative.Set2,
            hover_data={
                'index': df.index,
                'cluster': labels
            }
        )
        
        fig.update_traces(
            marker=dict(
                size=8,
                line=dict(width=2, color='DarkSlateGrey')
            )
        )
        
        fig.update_layout(
            height=700,
            scene=dict(
                camera=dict(
                    up=dict(x=0, y=0, z=1),
                    center=dict(x=0, y=0, z=0),
                    eye=dict(x=1.5, y=1.5, z=1.5)
                )
            )
        )
        
        return json.loads(fig.to_json())
    
    def _create_parallel_coordinates(self, df: pd.DataFrame) -> Dict:
        """Create parallel coordinates plot"""
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if len(numeric_cols) < 2:
            return {"error": "Need at least 2 numeric columns"}
        
        fig = px.parallel_coordinates(
            df[numeric_cols[:4]],  # Use first 4 numeric columns
            color=numeric_cols[0] if numeric_cols else None,
            title="📐 Parallel Coordinates Plot",
            color_continuous_scale=px.colors.diverging.Tealrose,
            dimensions=numeric_cols[:4]
        )
        
        fig.update_layout(height=500)
        return json.loads(fig.to_json())
    
    def _create_sunburst_chart(self, df: pd.DataFrame) -> Dict:
        """Create hierarchical sunburst chart"""
        # Create hierarchy from available columns
        hierarchy_cols = []
        for col in ['department', 'category', 'status', 'type']:
            if col in df.columns:
                hierarchy_cols.append(col)
        
        if len(hierarchy_cols) < 2:
            return {"error": "Need hierarchical columns"}
        
        fig = px.sunburst(
            df,
            path=hierarchy_cols[:3],  # Max 3 levels
            values='availability' if 'availability' in df.columns else None,
            title="☀️ Hierarchical Sunburst View",
            color_continuous_scale='RdBu',
            maxdepth=3
        )
        
        fig.update_layout(height=600)
        return json.loads(fig.to_json())
    
    def _create_animated_timeseries(self, df: pd.DataFrame) -> Dict:
        """Create animated time series visualization"""
        # Generate sample time series data if none exists
        if 'date' not in df.columns:
            dates = pd.date_range(start='2024-01-01', periods=30, freq='D')
            categories = df['department'].unique() if 'department' in df.columns else ['Category']
            
            animated_data = []
            for date in dates:
                for category in categories[:3]:  # Limit to 3 categories
                    animated_data.append({
                        'date': date,
                        'category': category,
                        'value': np.random.uniform(70, 100),
                        'trend': np.random.uniform(-2, 2)
                    })
            
            df_anim = pd.DataFrame(animated_data)
        else:
            df_anim = df.copy()
        
        fig = px.scatter(
            df_anim,
            x='date',
            y='value',
            animation_frame=df_anim['date'].dt.strftime('%b %d'),
            animation_group='category',
            color='category',
            size='trend',
            hover_name='category',
            title="🎬 Animated Time Series",
            size_max=20,
            range_y=[df_anim['value'].min() * 0.9, df_anim['value'].max() * 1.1]
        )
        
        # Add trend line
        if len(df_anim) > 10:
            for category in df_anim['category'].unique():
                cat_data = df_anim[df_anim['category'] == category]
                fig.add_traces(
                    px.line(cat_data, x='date', y='value').data
                )
        
        fig.update_layout(height=500)
        return json.loads(fig.to_json())
    
    def _create_violin_box_plot(self, df: pd.DataFrame) -> Dict:
        """Create combined violin and box plot"""
        if 'availability' not in df.columns or 'department' not in df.columns:
            return {"error": "Need availability and department columns"}
        
        fig = go.Figure()
        
        # Add violin plot
        fig.add_trace(go.Violin(
            x=df['department'],
            y=df['availability'],
            name='Violin',
            box_visible=True,
            line_color='black',
            fillcolor='lightseagreen',
            opacity=0.6
        ))
        
        # Add box plot
        fig.add_trace(go.Box(
            x=df['department'],
            y=df['availability'],
            name='Box',
            boxpoints='outliers',
            jitter=0.3,
            pointpos=-1.8,
            marker_color='rgb(107,174,214)',
            line_color='rgb(107,174,214)'
        ))
        
        fig.update_layout(
            title="🎻 Violin + Box Plot Comparison",
            xaxis_title="Department",
            yaxis_title="Availability (%)",
            height=500,
            showlegend=True
        )
        
        return json.loads(fig.to_json())
    
    # ========== DASH: INTERACTIVE WEB DASHBOARD ==========
    
    def create_dash_app(self, data: List[Dict]) -> dash.Dash:
        """
        Create a complete interactive Dash web application
        """
        logger.info("🚀 Creating interactive Dash application...")
        
        df = pd.DataFrame(data)
        
        # Initialize Dash app with Bootstrap
        app = dash.Dash(
            __name__,
            external_stylesheets=[dbc.themes.BOOTSTRAP],
            suppress_callback_exceptions=True
        )
        
        # App layout
        app.layout = dbc.Container([
            # Header
            dbc.Navbar(
                dbc.Container([
                    html.H1("📊 Advanced Equipment Analytics Dashboard", 
                           className="navbar-brand mb-0 h1"),
                    dbc.NavbarToggler(id="navbar-toggler"),
                ]),
                color="primary",
                dark=True,
                className="mb-4"
            ),
            
            # Main content
            dbc.Row([
                # Left sidebar - Controls
                dbc.Col([
                    dbc.Card([
                        dbc.CardHeader("🎛️ Dashboard Controls"),
                        dbc.CardBody([
                            html.H5("Chart Type", className="card-title"),
                            dcc.Dropdown(
                                id='chart-type-dropdown',
                                options=[
                                    {'label': '3D Cluster', 'value': '3d'},
                                    {'label': 'Parallel Coordinates', 'value': 'parallel'},
                                    {'label': 'Sunburst', 'value': 'sunburst'},
                                    {'label': 'Violin Box', 'value': 'violin'},
                                    {'label': 'AI Text Analysis', 'value': 'ai'}
                                ],
                                value='3d',
                                className="mb-3"
                            ),
                            
                            html.H5("Filters", className="card-title mt-4"),
                            dcc.RangeSlider(
                                id='availability-slider',
                                min=0,
                                max=100,
                                step=5,
                                value=[70, 100],
                                marks={i: f'{i}%' for i in range(0, 101, 20)},
                                className="mb-3"
                            ),
                            
                            html.H5("AI Analysis", className="card-title mt-4"),
                            dbc.Textarea(
                                id='ai-text-input',
                                placeholder="Enter maintenance notes for AI analysis...",
                                rows=3,
                                className="mb-3"
                            ),
                            dbc.Button(
                                "🧠 Analyze with AI",
                                id='analyze-button',
                                color="success",
                                className="w-100"
                            ),
                        ])
                    ], className="mb-4"),
                    
                    # Stats Card
                    dbc.Card([
                        dbc.CardHeader("📈 Quick Stats"),
                        dbc.CardBody([
                            html.H6(f"Total Records: {len(df)}", className="card-text"),
                            html.H6(f"Avg Availability: {df['availability'].mean():.1f}%" 
                                   if 'availability' in df.columns else "N/A", 
                                   className="card-text"),
                            html.H6(f"Departments: {df['department'].nunique()}" 
                                   if 'department' in df.columns else "N/A", 
                                   className="card-text"),
                        ])
                    ])
                ], width=3),
                
                # Main chart area
                dbc.Col([
                    dbc.Row([
                        dbc.Col([
                            dbc.Card([
                                dbc.CardHeader("🎯 Main Visualization"),
                                dbc.CardBody([
                                    dcc.Graph(
                                        id='main-chart',
                                        style={'height': '600px'}
                                    )
                                ])
                            ])
                        ], width=8),
                        
                        dbc.Col([
                            dbc.Card([
                                dbc.CardHeader("📊 Side Charts"),
                                dbc.CardBody([
                                    dcc.Graph(id='side-chart-1'),
                                    dcc.Graph(id='side-chart-2'),
                                ])
                            ])
                        ], width=4)
                    ]),
                    
                    # AI Analysis Results
                    dbc.Row([
                        dbc.Col([
                            dbc.Card([
                                dbc.CardHeader("🤖 AI Analysis Results"),
                                dbc.CardBody([
                                    html.Div(id='ai-results'),
                                    dcc.Graph(id='sentiment-chart')
                                ])
                            ])
                        ])
                    ], className="mt-4")
                ], width=9)
            ]),
            
            # Hidden stores
            dcc.Store(id='data-store', data=df.to_dict('records')),
            dcc.Store(id='ai-analysis-store'),
            
            # Interval for updates
            dcc.Interval(
                id='interval-component',
                interval=30*1000,  # 30 seconds
                n_intervals=0
            )
        ], fluid=True)
        
        # ========== DASH CALLBACKS ==========
        
        @app.callback(
            Output('main-chart', 'figure'),
            [Input('chart-type-dropdown', 'value'),
             Input('availability-slider', 'value'),
             Input('data-store', 'data')]
        )
        def update_main_chart(chart_type, availability_range, stored_data):
            """Update main chart based on selections"""
            df_filtered = pd.DataFrame(stored_data)
            
            # Apply availability filter
            if 'availability' in df_filtered.columns:
                df_filtered = df_filtered[
                    (df_filtered['availability'] >= availability_range[0]) &
                    (df_filtered['availability'] <= availability_range[1])
                ]
            
            # Generate chart based on type
            if chart_type == '3d':
                fig = self._create_3d_cluster_plot(df_filtered)
                return go.Figure(fig) if isinstance(fig, dict) and 'data' in fig else go.Figure()
            elif chart_type == 'parallel':
                fig = self._create_parallel_coordinates(df_filtered)
                return go.Figure(fig) if isinstance(fig, dict) and 'data' in fig else go.Figure()
            elif chart_type == 'sunburst':
                fig = self._create_sunburst_chart(df_filtered)
                return go.Figure(fig) if isinstance(fig, dict) and 'data' in fig else go.Figure()
            elif chart_type == 'violin':
                fig = self._create_violin_box_plot(df_filtered)
                return go.Figure(fig) if isinstance(fig, dict) and 'data' in fig else go.Figure()
            else:
                return go.Figure()
        
        @app.callback(
            [Output('ai-results', 'children'),
             Output('sentiment-chart', 'figure'),
             Output('ai-analysis-store', 'data')],
            [Input('analyze-button', 'n_clicks')],
            [State('ai-text-input', 'value')]
        )
        def analyze_text_with_ai(n_clicks, text_input):
            """Analyze text with AI/Transformers"""
            if n_clicks is None or not text_input:
                return "Enter text above to analyze...", go.Figure(), None
            
            # Split text into sentences/lines
            texts = [t.strip() for t in text_input.split('\n') if t.strip()]
            
            # Analyze with transformers
            results = self.analyze_text_with_transformers(texts)
            
            if 'error' in results:
                return f"Error: {results['error']}", go.Figure(), None
            
            # Create results display
            results_display = [
                html.H5(f"📝 Analyzed {results['total_texts']} texts"),
                html.P(f"✅ Positive: {results['positive_count']}"),
                html.P(f"⚠️ Negative: {results['negative_count']}"),
                html.Hr(),
            ]
            
            # Add sample analyses
            for i, analysis in enumerate(results['analyses'][:3]):  # Show first 3
                sentiment_color = "green" if analysis['sentiment'] == "POSITIVE" else "red"
                results_display.append(
                    html.Div([
                        html.P(f"\"{analysis['text']}\""),
                        html.Span(
                            f"Sentiment: {analysis['sentiment']} ({analysis['sentiment_score']:.2%})",
                            style={'color': sentiment_color, 'fontWeight': 'bold'}
                        ),
                        html.Hr() if i < 2 else None
                    ])
                )
            
            # Create sentiment distribution chart
            if results['analyses']:
                sentiment_df = pd.DataFrame(results['analyses'])
                fig = px.pie(
                    sentiment_df,
                    names='sentiment',
                    title='Sentiment Distribution',
                    color='sentiment',
                    color_discrete_map={'POSITIVE': 'green', 'NEGATIVE': 'red'}
                )
            else:
                fig = go.Figure()
            
            return results_display, fig, results
        
        logger.info("✅ Dash application created successfully")
        return app
    
    # ========== MATPLOTLIB + SEABORN: STATIC VISUALIZATIONS ==========
    
    def create_publication_quality_charts(self, data: List[Dict]) -> Dict[str, str]:
        """
        Create high-quality static charts for reports/publications
        Returns base64 encoded images
        """
        logger.info("🖼️ Creating publication-quality charts...")
        
        df = pd.DataFrame(data)
        charts = {}
        
        # 1. Correlation Heatmap (Seaborn)
        if len(df.select_dtypes(include=[np.number]).columns) >= 3:
            charts['correlation_heatmap'] = self._create_correlation_heatmap(df)
        
        # 2. Distribution Plots (Matplotlib + Seaborn)
        charts['distribution_grid'] = self._create_distribution_grid(df)
        
        # 3. Pair Plot (Seaborn)
        charts['pair_plot'] = self._create_pair_plot(df)
        
        # 4. Regression Plot (Seaborn)
        charts['regression_plot'] = self._create_regression_plot(df)
        
        # 5. Multi-panel Figure (Matplotlib)
        charts['multi_panel'] = self._create_multi_panel_figure(df)
        
        logger.info(f"✅ Created {len(charts)} publication-quality charts")
        return charts
    
    def _create_correlation_heatmap(self, df: pd.DataFrame) -> str:
        """Create correlation heatmap with Seaborn"""
        numeric_df = df.select_dtypes(include=[np.number])
        if len(numeric_df.columns) < 2:
            return ""
        
        plt.figure(figsize=(10, 8))
        correlation = numeric_df.corr()
        
        # Create heatmap
        mask = np.triu(np.ones_like(correlation, dtype=bool))
        sns.heatmap(
            correlation,
            mask=mask,
            annot=True,
            fmt=".2f",
            cmap='coolwarm',
            center=0,
            square=True,
            linewidths=.5,
            cbar_kws={"shrink": .8}
        )
        
        plt.title('Correlation Heatmap', fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        return self._fig_to_base64()
    
    def _create_distribution_grid(self, df: pd.DataFrame) -> str:
        """Create grid of distribution plots"""
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if not numeric_cols:
            return ""
        
        # Limit to 6 columns for readability
        cols_to_plot = numeric_cols[:6]
        n_cols = min(3, len(cols_to_plot))
        n_rows = (len(cols_to_plot) + n_cols - 1) // n_cols
        
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(15, 4 * n_rows))
        axes = axes.flatten() if n_rows > 1 or n_cols > 1 else [axes]
        
        for idx, col in enumerate(cols_to_plot):
            ax = axes[idx]
            
            # Histogram + KDE
            sns.histplot(df[col], kde=True, ax=ax, color='skyblue', stat='density')
            
            # Add boxplot on top
            box_ax = ax.twinx()
            sns.boxplot(x=df[col], ax=box_ax, color='orange', width=0.3)
            box_ax.set_yticks([])
            
            ax.set_title(f'Distribution of {col}', fontsize=12)
            ax.set_xlabel('')
            
            # Add statistics text
            stats_text = f'Mean: {df[col].mean():.2f}\nStd: {df[col].std():.2f}'
            ax.text(0.02, 0.98, stats_text, transform=ax.transAxes,
                   verticalalignment='top', fontsize=9,
                   bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        
        # Hide unused subplots
        for idx in range(len(cols_to_plot), len(axes)):
            axes[idx].set_visible(False)
        
        plt.suptitle('Distribution Analysis Grid', fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        return self._fig_to_base64()
    
    def _create_pair_plot(self, df: pd.DataFrame) -> str:
        """Create pair plot (scatter matrix)"""
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if len(numeric_cols) < 2:
            return ""
        
        # Limit to 5 columns for readability
        cols_to_plot = numeric_cols[:5]
        
        # Create pair plot with hue if available
        hue_col = None
        for col in ['department', 'category', 'status']:
            if col in df.columns and df[col].nunique() <= 6:
                hue_col = col
                break
        
        pair_grid = sns.pairplot(
            df[cols_to_plot + ([hue_col] if hue_col else [])],
            hue=hue_col,
            diag_kind='kde',
            plot_kws={'alpha': 0.6, 's': 50},
            diag_kws={'fill': True}
        )
        
        pair_grid.fig.suptitle('Pair Plot Matrix', y=1.02, fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        return self._fig_to_base64(pair_grid.fig)
    
    def _create_regression_plot(self, df: pd.DataFrame) -> str:
        """Create regression plot with confidence intervals"""
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if len(numeric_cols) < 2:
            return ""
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        
        # Simple linear regression
        x_col, y_col = numeric_cols[0], numeric_cols[1]
        sns.regplot(data=df, x=x_col, y=y_col, ax=ax1, scatter_kws={'alpha': 0.6})
        ax1.set_title(f'{y_col} vs {x_col} (Linear Regression)', fontsize=12)
        
        # Residual plot
        if len(numeric_cols) >= 3:
            z_col = numeric_cols[2]
            sns.residplot(data=df, x=x_col, y=z_col, ax=ax2, scatter_kws={'alpha': 0.6})
            ax2.set_title(f'{z_col} Residuals', fontsize=12)
            ax2.axhline(y=0, color='red', linestyle='--', alpha=0.5)
        else:
            ax2.set_visible(False)
        
        plt.suptitle('Regression Analysis', fontsize=16, fontweight='bold')
        plt.tight_layout()
        
        return self._fig_to_base64()
    
    def _create_multi_panel_figure(self, df: pd.DataFrame) -> str:
        """Create multi-panel figure with different chart types"""
        fig = plt.figure(figsize=(16, 10))
        
        # 1. Time series (if date column exists)
        gs = fig.add_gridspec(2, 3)
        
        # Panel 1: Line plot
        ax1 = fig.add_subplot(gs[0, :2])
        if 'availability' in df.columns:
            if 'date' in df.columns:
                df_sorted = df.sort_values('date')
                ax1.plot(df_sorted['date'], df_sorted['availability'], marker='o')
                ax1.set_title('Availability Trend', fontsize=12)
                ax1.tick_params(axis='x', rotation=45)
            else:
                ax1.plot(df['availability'].values, marker='o')
                ax1.set_title('Availability Values', fontsize=12)
        
        # Panel 2: Bar plot
        ax2 = fig.add_subplot(gs[0, 2])
        if 'department' in df.columns:
            dept_counts = df['department'].value_counts()
            ax2.bar(dept_counts.index, dept_counts.values, color='skyblue')
            ax2.set_title('Equipment by Department', fontsize=12)
            ax2.tick_params(axis='x', rotation=45)
        
        # Panel 3: Pie chart
        ax3 = fig.add_subplot(gs[1, 0])
        if 'status' in df.columns:
            status_counts = df['status'].value_counts()
            ax3.pie(status_counts.values, labels=status_counts.index, autopct='%1.1f%%')
            ax3.set_title('Status Distribution', fontsize=12)
        
        # Panel 4: Box plot
        ax4 = fig.add_subplot(gs[1, 1])
        if 'availability' in df.columns and 'department' in df.columns:
            df.boxplot(column='availability', by='department', ax=ax4)
            ax4.set_title('Availability by Department', fontsize=12)
            ax4.tick_params(axis='x', rotation=45)
        
        # Panel 5: Scatter plot
        ax5 = fig.add_subplot(gs[1, 2])
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if len(numeric_cols) >= 2:
            ax5.scatter(df[numeric_cols[0]], df[numeric_cols[1]], alpha=0.6)
            ax5.set_xlabel(numeric_cols[0])
            ax5.set_ylabel(numeric_cols[1])
            ax5.set_title(f'{numeric_cols[1]} vs {numeric_cols[0]}', fontsize=12)
        
        plt.suptitle('Multi-Panel Equipment Analysis Dashboard', fontsize=18, fontweight='bold')
        plt.tight_layout()
        
        return self._fig_to_base64()
    
    def _fig_to_base64(self, fig=None) -> str:
        """Convert matplotlib figure to base64 string"""
        if fig is None:
            fig = plt.gcf()
        
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=self.config.matplotlib_dpi, 
                   bbox_inches='tight', facecolor='white')
        plt.close(fig)
        
        buf.seek(0)
        img_str = base64.b64encode(buf.read()).decode('utf-8')
        return f"data:image/png;base64,{img_str}"
    
    # ========== NUMPY: ADVANCED COMPUTATIONS ==========
    
    def perform_advanced_computations(self, data: List[Dict]) -> Dict:
        """
        Demonstrate NumPy's power for numerical computations
        """
        logger.info("🧮 Performing advanced NumPy computations...")
        
        # Convert to NumPy arrays for fast computation
        df = pd.DataFrame(data)
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        
        if len(numeric_cols) == 0:
            return {"error": "No numeric columns found"}
        
        # Convert to NumPy array
        array_data = df[numeric_cols].to_numpy()
        
        results = {
            "array_shape": array_data.shape,
            "array_dtype": str(array_data.dtype),
            "computations": {}
        }
        
        # 1. Statistical analysis
        results["computations"]["statistics"] = {
            "mean": np.mean(array_data, axis=0).tolist(),
            "std": np.std(array_data, axis=0).tolist(),
            "min": np.min(array_data, axis=0).tolist(),
            "max": np.max(array_data, axis=0).tolist(),
            "median": np.median(array_data, axis=0).tolist(),
            "percentile_25": np.percentile(array_data, 25, axis=0).tolist(),
            "percentile_75": np.percentile(array_data, 75, axis=0).tolist(),
        }
        
        # 2. Linear algebra operations
        if array_data.shape[1] >= 2:
            covariance = np.cov(array_data.T)
            correlation = np.corrcoef(array_data.T)
            
            results["computations"]["linear_algebra"] = {
                "covariance_matrix": covariance.tolist(),
                "correlation_matrix": correlation.tolist(),
                "matrix_shape": covariance.shape,
            }
        
        # 3. Fourier transform (if time series)
        if 'availability' in df.columns and len(df) > 10:
            availability_data = df['availability'].to_numpy()
            fft_result = np.fft.fft(availability_data)
            
            results["computations"]["fourier_analysis"] = {
                "fft_magnitude": np.abs(fft_result).tolist(),
                "dominant_frequency": float(np.argmax(np.abs(fft_result[1:len(fft_result)//2]))),
                "signal_energy": float(np.sum(availability_data**2)),
            }
        
        # 4. Optimization example
        if array_data.shape[1] >= 2:
            # Simple linear regression using least squares
            X = array_data[:, :-1]
            y = array_data[:, -1]
            
            # Add intercept
            X_with_intercept = np.column_stack([np.ones(X.shape[0]), X])
            
            # Solve normal equations: (X'X)β = X'y
            try:
                coefficients = np.linalg.lstsq(X_with_intercept, y, rcond=None)[0]
                results["computations"]["optimization"] = {
                    "regression_coefficients": coefficients.tolist(),
                    "r_squared": 1 - np.sum((y - X_with_intercept @ coefficients)**2) / np.sum((y - np.mean(y))**2)
                }
            except np.linalg.LinAlgError:
                results["computations"]["optimization"] = {"error": "Matrix is singular"}
        
        logger.info("✅ Advanced NumPy computations completed")
        return results
    
    # ========== MAIN ENTRY POINT ==========
    
    def generate_all_visualizations(self, data: List[Dict]) -> Dict:
        """
        Generate all types of visualizations from the data
        Returns a dictionary with all visualization outputs
        """
        logger.info("🚀 Starting comprehensive visualization pipeline...")
        
        results = {
            "timestamp": datetime.now().isoformat(),
            "data_summary": {
                "total_records": len(data),
                "processing_start": datetime.now().isoformat()
            }
        }
        
        try:
            # 1. Polars Processing (Ultra-fast)
            logger.info("📊 Step 1/6: Polars data processing...")
            polars_df = self.process_with_polars(data)
            results["polars_processing"] = {
                "summary": polars_df.describe().to_dict() if hasattr(polars_df, 'describe') else {},
                "shape": polars_df.shape if hasattr(polars_df, 'shape') else "N/A"
            }
            
            # 2. NumPy Advanced Computations
            logger.info("🧮 Step 2/6: NumPy computations...")
            results["numpy_computations"] = self.perform_advanced_computations(data)
            
            # 3. Plotly Interactive Visualizations
            logger.info("📈 Step 3/6: Plotly interactive charts...")
            results["plotly_charts"] = self.create_interactive_dashboard(data)
            
            # 4. Matplotlib/Seaborn Static Charts
            logger.info("🖼️ Step 4/6: Publication-quality charts...")
            results["static_charts"] = self.create_publication_quality_charts(data)
            
            # 5. Transformers AI Analysis (if text data available)
            logger.info("🤖 Step 5/6: AI/Transformers analysis...")
            # Check if there's text data to analyze
            text_data = self._extract_text_data(data)
            if text_data:
                results["ai_analysis"] = self.analyze_text_with_transformers(text_data)
            else:
                results["ai_analysis"] = {"info": "No text data found for AI analysis"}
            
            # 6. Prepare Dash App (ready to run)
            logger.info("🌐 Step 6/6: Preparing Dash application...")
            dash_app = self.create_dash_app(data)
            results["dash_app"] = {
                "ready": True,
                "port": self.config.dash_port,
                "message": f"Run dash_app.run_server(port={self.config.dash_port}) to launch"
            }
            
            results["data_summary"]["processing_end"] = datetime.now().isoformat()
            results["data_summary"]["total_time_seconds"] = (
                datetime.fromisoformat(results["data_summary"]["processing_end"]) -
                datetime.fromisoformat(results["data_summary"]["processing_start"])
            ).total_seconds()
            
            logger.info("✅ All visualizations generated successfully!")
            
        except Exception as e:
            logger.error(f"❌ Error in visualization pipeline: {e}")
            results["error"] = str(e)
        
        return results
    
    def _extract_text_data(self, data: List[Dict]) -> List[str]:
        """Extract text fields from data for AI analysis"""
        text_fields = []
        
        # Common text field names in equipment data
        text_field_names = ['notes', 'description', 'comments', 'maintenance_notes',
                           'issue_description', 'remarks', 'observations']
        
        for item in data:
            for field in text_field_names:
                if field in item and isinstance(item[field], str) and item[field].strip():
                    text_fields.append(item[field].strip())
        
        return text_fields


# ========== UTILITY FUNCTIONS ==========

def run_dash_app(data: List[Dict], port: int = 8050) -> None:
    """Quick function to run the Dash app"""
    visualizer = AdvancedVisualizer()
    dash_app = visualizer.create_dash_app(data)
    
    print(f"🚀 Starting Dash server on http://localhost:{port}")
    print("📊 Open the URL in your browser to see the interactive dashboard")
    print("🛑 Press Ctrl+C to stop the server")
    
    dash_app.run_server(port=port, debug=False)

def export_all_charts(data: List[Dict], output_dir: str = "visualizations") -> Dict:
    """Export all charts to files"""
    import os
    import json
    
    visualizer = AdvancedVisualizer()
    results = visualizer.generate_all_visualizations(data)
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Save JSON results
    with open(os.path.join(output_dir, "visualizations_summary.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    # Save static charts as images
    static_charts_dir = os.path.join(output_dir, "static_charts")
    os.makedirs(static_charts_dir, exist_ok=True)
    
    if "static_charts" in results:
        for chart_name, chart_data in results["static_charts"].items():
            if chart_data.startswith("data:image/png;base64,"):
                # Save base64 image to file
                img_data = chart_data.split(",")[1]
                img_bytes = base64.b64decode(img_data)
                
                with open(os.path.join(static_charts_dir, f"{chart_name}.png"), "wb") as f:
                    f.write(img_bytes)
    
    print(f"✅ All visualizations exported to: {output_dir}")
    return results


# ========== DEMO/EXAMPLE USAGE ==========

if __name__ == "__main__":
    # Example usage
    print("🎨 Advanced Visualizations Module Demo")
    print("=" * 50)
    
    # Sample data (replace with your actual data)
    sample_data = [
        {
            "id": 1,
            "name": "CNC Machine 1",
            "department": "Production",
            "availability": 97.5,
            "status": "operational",
            "operational_hours": 450.5,
            "breakdown_hours": 12.3,
            "temperature": 65.2,
            "vibration": 4.5,
            "last_maintenance": "2024-01-15",
            "notes": "Machine running smoothly. Regular maintenance completed."
        },
        {
            "id": 2,
            "name": "Forklift A",
            "department": "Logistics",
            "availability": 85.2,
            "status": "maintenance",
            "operational_hours": 320.0,
            "breakdown_hours": 8.5,
            "temperature": 72.1,
            "vibration": 7.8,
            "last_maintenance": "2024-01-10",
            "notes": "Needs bearing replacement. High vibration detected."
        },
        {
            "id": 3,
            "name": "3D Printer",
            "department": "R&D",
            "availability": 91.8,
            "status": "operational",
            "operational_hours": 280.0,
            "breakdown_hours": 24.0,
            "temperature": 68.5,
            "vibration": 3.2,
            "last_maintenance": "2024-01-20",
            "notes": "Calibration needed. Print quality decreasing."
        }
    ]
    
    # Create visualizer
    visualizer = AdvancedVisualizer()
    
    # Generate all visualizations
    print("🚀 Generating all visualizations...")
    results = visualizer.generate_all_visualizations(sample_data)
    
    print("\n✅ Visualizations Generated:")
    print(f"   📊 Polars Processing: {results.get('polars_processing', {}).get('summary', 'Done')}")
    print(f"   🧮 NumPy Computations: {len(results.get('numpy_computations', {}))} calculations")
    print(f"   📈 Plotly Charts: {len(results.get('plotly_charts', {}))} interactive charts")
    print(f"   🖼️ Static Charts: {len(results.get('static_charts', {}))} publication charts")
    print(f"   🤖 AI Analysis: {results.get('ai_analysis', {}).get('total_texts', 0)} texts analyzed")
    print(f"   🌐 Dash App: {'Ready' if results.get('dash_app', {}).get('ready') else 'Not ready'}")
    
    print("\n📁 To run the interactive Dash dashboard:")
    print("   from visualizations import run_dash_app")
    print("   run_dash_app(your_data, port=8050)")
    
    print("\n📥 To export all charts:")
    print("   from visualizations import export_all_charts")
    print("   export_all_charts(your_data, 'output_folder')")